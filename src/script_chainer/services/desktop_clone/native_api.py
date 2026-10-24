"""桌面分身用到的 Windows 原生接口。

桌面分身的原理与 BetterGenshinImpact 相同：
1. 用 WTSEnableChildSessions 打开 Windows 的「子会话」（远程桌面用的第二个桌面）；
2. 用远程桌面控件连到本机的这个子会话，于是第二个桌面就变成可以嵌到窗口里的画面；
3. 用计划任务把脚本/游戏丢到子会话里运行。

本模块只负责第 1 步与相关的环境检查，全部走 ctypes / winreg，不依赖 pywin32。
"""

import ctypes
import os
import sys
from ctypes import wintypes
from dataclasses import dataclass

from one_dragon.utils.log_utils import log

ERROR_NOT_FOUND = 1168
NO_SESSION_ID = 0xFFFFFFFF

RDP_TCP_KEY = r"SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp"
TERMINAL_SERVER_KEY = r"SYSTEM\CurrentControlSet\Control\Terminal Server"
TERMINAL_SERVER_POLICY_KEY = r"SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services"
PASSWORDLESS_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\PasswordLess\Device"
RDPWRAP_DIRS = [
    os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "RDP Wrapper"),
    os.path.join(
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), "RDP Wrapper"
    ),
]

_wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_shell32 = ctypes.WinDLL("shell32", use_last_error=True)

_wtsapi32.WTSEnableChildSessions.argtypes = [wintypes.BOOL]
_wtsapi32.WTSEnableChildSessions.restype = wintypes.BOOL

_wtsapi32.WTSIsChildSessionsEnabled.argtypes = [ctypes.POINTER(wintypes.BOOL)]
_wtsapi32.WTSIsChildSessionsEnabled.restype = wintypes.BOOL

_wtsapi32.WTSGetChildSessionId.argtypes = [ctypes.POINTER(wintypes.DWORD)]
_wtsapi32.WTSGetChildSessionId.restype = wintypes.BOOL

_wtsapi32.WTSLogoffSession.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL]
_wtsapi32.WTSLogoffSession.restype = wintypes.BOOL

_kernel32.ProcessIdToSessionId.argtypes = [
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
_kernel32.ProcessIdToSessionId.restype = wintypes.BOOL

_shell32.IsUserAnAdmin.argtypes = []
_shell32.IsUserAnAdmin.restype = wintypes.BOOL


@dataclass
class EnvironmentIssue:
    """一条环境检查结果。"""

    title: str
    message: str
    fatal: bool = False
    """为 True 时无法继续（例如不是管理员）。"""


def is_user_admin() -> bool:
    """当前进程是否以管理员身份运行。开启子会话、建计划任务都需要。"""
    try:
        return bool(_shell32.IsUserAnAdmin())
    except OSError:
        return False


def is_child_sessions_enabled() -> bool:
    """Windows 的「子会话」开关是否已经打开。"""
    enabled = wintypes.BOOL(0)
    if not _wtsapi32.WTSIsChildSessionsEnabled(ctypes.byref(enabled)):
        log.warning("查询子会话开关失败，按未开启处理")
        return False
    return bool(enabled.value)


def enable_child_sessions() -> bool:
    """打开子会话开关，成功返回 True。"""
    if not _wtsapi32.WTSEnableChildSessions(True):
        log.error(f"开启子会话失败，错误码 {ctypes.get_last_error()}")
        return False
    return True


def get_child_session_id() -> int:
    """当前子会话 id，没有子会话时返回 0。"""
    session_id = wintypes.DWORD(NO_SESSION_ID)
    if not _wtsapi32.WTSGetChildSessionId(ctypes.byref(session_id)):
        error = ctypes.get_last_error()
        if error not in (0, ERROR_NOT_FOUND):
            log.warning(f"获取子会话 id 失败，错误码 {error}")
        return 0

    if session_id.value == NO_SESSION_ID:
        return 0
    return int(session_id.value)


def logoff_child_session(session_id: int) -> bool:
    """注销指定的子会话。"""
    if not session_id:
        return False
    return bool(_wtsapi32.WTSLogoffSession(None, wintypes.DWORD(session_id), True))


def current_session_id() -> int:
    """当前进程所在的会话 id。"""
    session_id = wintypes.DWORD(0)
    if not _kernel32.ProcessIdToSessionId(
        wintypes.DWORD(os.getpid()), ctypes.byref(session_id)
    ):
        return 0
    return int(session_id.value)


def _read_dword(key_path: str, value_name: str) -> int | None:
    """读注册表里的 DWORD，读不到返回 None。"""
    import winreg

    try:
        with winreg.OpenKeyEx(
            winreg.HKEY_LOCAL_MACHINE,
            key_path,
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as key:
            value, _ = winreg.QueryValueEx(key, value_name)
            return int(value)
    except (OSError, ValueError):
        return None


def is_rdp_denied() -> bool:
    """是否禁用了远程桌面（fDenyTSConnections = 1）。

    组策略的那一份优先，与「设置」界面显示的状态一致。
    """
    policy_value = _read_dword(TERMINAL_SERVER_POLICY_KEY, "fDenyTSConnections")
    if policy_value is not None:
        return policy_value == 1
    return _read_dword(TERMINAL_SERVER_KEY, "fDenyTSConnections") == 1


def rdp_port() -> int:
    """远程桌面端口，默认 3389。"""
    value = _read_dword(RDP_TCP_KEY, "PortNumber")
    if value is None or value <= 0 or value > 65535:
        return 3389
    return value


def is_passwordless_hello_only() -> bool:
    """是否开启了「仅允许 Windows Hello 无密码登录」。

    这种账户无法通过远程桌面（含子会话）登录，需要先在系统设置里关掉。
    """
    value = _read_dword(PASSWORDLESS_KEY, "DevicePasswordLessBuildVersion")
    return value == 2


def is_rdpwrap_enabled() -> bool:
    """是否安装了 RDP Wrapper（与子会话方案的远程桌面冲突）。"""
    return any(dir_path and os.path.isdir(dir_path) for dir_path in RDPWRAP_DIRS)


def collect_environment_issues(
    child_sessions_enabled: bool | None = None,
) -> list[EnvironmentIssue]:
    """桌面分身的启动前检查。

    Args:
        child_sessions_enabled: 已经查过的子会话开关状态，None 表示现场查一次。

    Returns:
        检查结果列表，为空表示可以直接启动。
    """
    issues: list[EnvironmentIssue] = []

    if sys.platform != "win32":
        issues.append(
            EnvironmentIssue(
                title="当前不是 Windows",
                message="桌面分身依赖 Windows 的会话机制，只能在 Windows 上使用。",
                fatal=True,
            )
        )
        return issues

    if not is_user_admin():
        issues.append(
            EnvironmentIssue(
                title="需要管理员权限",
                message="开启 Windows 子会话、在分身里启动程序都需要管理员权限，\n"
                "请以管理员身份重新运行本程序。",
                fatal=True,
            )
        )

    enabled = (
        is_child_sessions_enabled()
        if child_sessions_enabled is None
        else child_sessions_enabled
    )
    if not enabled and is_user_admin():
        issues.append(
            EnvironmentIssue(
                title="尚未开启 Windows 子会话",
                message="启动桌面分身时会自动开启（可能需要注销后重新登录一次才生效）。",
            )
        )

    if is_rdp_denied():
        issues.append(
            EnvironmentIssue(
                title="远程桌面被禁用",
                message="桌面分身要用到远程桌面服务，请在「系统设置 - 远程桌面」里打开，\n"
                f"或把注册表 {TERMINAL_SERVER_KEY} 下的 fDenyTSConnections 改为 0。",
                fatal=True,
            )
        )

    if is_passwordless_hello_only():
        # 只当作提示：子会话复用当前登录凭据，多数情况下仍能连上，
        # 真连不上时宿主会把远程桌面返回的原因显示出来。
        issues.append(
            EnvironmentIssue(
                title="账户只能使用 Windows Hello 无密码登录",
                message="这种账户用远程桌面登录时可能要求密码，导致桌面分身连不上。\n"
                "如果连接失败，请在「设置 - 账户 - 登录选项」中关闭\n"
                "「为了提高安全性，仅允许对此设备上的 Microsoft 账户使用 Windows Hello 登录」。",
            )
        )

    if is_rdpwrap_enabled():
        issues.append(
            EnvironmentIssue(
                title="检测到 RDP Wrapper",
                message="RDP Wrapper 会占用远程桌面服务，可能与桌面分身冲突，建议先卸载再使用。",
            )
        )

    return issues


def describe_environment() -> str:
    """给界面用的一句话环境摘要。"""
    if sys.platform != "win32":
        return "当前系统不支持桌面分身"

    parts = []
    parts.append("管理员" if is_user_admin() else "非管理员")
    parts.append("子会话已开启" if is_child_sessions_enabled() else "子会话未开启")
    parts.append(f"远程桌面端口 {rdp_port()}")
    session_id = current_session_id()
    if session_id:
        parts.append(f"当前会话 {session_id}")
    child_id = get_child_session_id()
    parts.append(f"分身会话 {child_id}" if child_id else "无分身会话")
    return " · ".join(parts)
