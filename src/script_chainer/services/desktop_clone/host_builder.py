"""定位（必要时编译）桌面分身宿主程序。

宿主程序是 assets/desktop_clone/host 下的几个 C# 文件，用 Windows 自带的
.NET Framework 编译器 csc.exe 现场编译成 exe：

* 不把 exe 提交进仓库，避免二进制文件；
* Windows 10/11 自带 .NET Framework 4.x，编译不需要装任何东西；
* 编译结果按源码哈希缓存在 %LOCALAPPDATA%\\OneDragonScriptChainer\\desktop_clone，
  源码没变就直接复用，也不会污染程序目录（安装到 Program Files 时目录可能是只读的）。

打包发布时也可以把编译好的 exe 放到 assets/desktop_clone/bin/DesktopCloneHost.exe，
此时优先使用它，用户机器上就不需要编译。
"""

import contextlib
import hashlib
import os
import subprocess
import sys
import tempfile

from one_dragon.utils import os_utils
from one_dragon.utils.log_utils import log

HOST_RESOURCE_DIR = ("assets", "desktop_clone", "host")
PREBUILT_RESOURCE_PATH = ("assets", "desktop_clone", "bin", "DesktopCloneHost.exe")
ENV_HOST_OVERRIDE = "ONE_DRAGON_DESKTOP_CLONE_HOST"

HOST_SOURCES = [
    "NativeMethods.cs",
    "ProtocolWriter.cs",
    "StdinLineReader.cs",
    "RdpClient.cs",
    "SessionLauncher.cs",
    "WindowHosting.cs",
    "HostForm.cs",
    "Program.cs",
]

FRAMEWORK_ASSEMBLIES = [
    "System.dll",
    "System.Core.dll",
    "System.Windows.Forms.dll",
    "System.Drawing.dll",
]

COMPILE_TIMEOUT_SECONDS = 180
CREATE_NO_WINDOW = 0x08000000
CREATE_PROCESS_FAILED_CODE = 0xC0000142


class HostBuildError(RuntimeError):
    """宿主程序定位/编译失败。"""


def find_csc() -> str | None:
    """找到 .NET Framework 自带的 csc.exe。"""
    windows_dir = os.environ.get("WINDIR", r"C:\Windows")
    for framework in ("Framework64", "Framework"):
        csc_path = os.path.join(
            windows_dir, "Microsoft.NET", framework, "v4.0.30319", "csc.exe"
        )
        if os.path.isfile(csc_path):
            return csc_path
    return None


def host_source_dir() -> str:
    return os_utils.get_resource_path(*HOST_RESOURCE_DIR)


def cache_dir() -> str:
    """宿主程序的缓存目录，优先放 LOCALAPPDATA，写不了就退回工作目录。"""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base = os.path.join(local_app_data, "OneDragonScriptChainer", "desktop_clone")
        try:
            os.makedirs(base, exist_ok=True)
            if _is_writable(base):
                return base
            log.warning(f"{base} 不可写，桌面分身宿主程序改放到工作目录缓存")
        except OSError as e:
            log.warning(f"无法使用 {base} 缓存桌面分身宿主程序：{e}")
    base = os_utils.get_path_under_work_dir("cache", "desktop_clone")
    os.makedirs(base, exist_ok=True)
    return base


def _is_writable(directory: str) -> bool:
    """真正建一个临时文件确认目录可写：只看 os.access 会被沙箱/只读挂载骗过。

    注意这里刻意不用 tempfile.mkstemp：某些受限环境下在不可写目录里创建
    临时文件会卡住而不是直接报错。
    """
    probe = os.path.join(directory, f"probe_{os.getpid()}.tmp")
    try:
        with open(probe, "wb"):
            pass
    except OSError:
        return False

    with contextlib.suppress(OSError):
        os.remove(probe)
    return True


def source_signature(src_dir: str) -> str:
    """源码内容 + 文件名 + Python 端协议版本的哈希，用于缓存 exe。"""
    digest = hashlib.md5()
    digest.update(b"OneDragonDesktopCloneHost-v1")
    for name in sorted(HOST_SOURCES):
        path = os.path.join(src_dir, name)
        if not os.path.isfile(path):
            raise HostBuildError(f"缺少桌面分身宿主源码：{path}")
        digest.update(name.encode("utf-8"))
        with open(path, "rb") as f:
            digest.update(f.read())
    return digest.hexdigest()[:12]


def resolve_host_exe() -> str:
    """返回可用的 DesktopCloneHost.exe 路径，必要时现场编译。

    Raises:
        HostBuildError: 找不到源码或编译失败。
    """
    override = os.environ.get(ENV_HOST_OVERRIDE)
    if override and os.path.isfile(override):
        return override

    prebuilt = os_utils.get_resource_path(*PREBUILT_RESOURCE_PATH)
    if os.path.isfile(prebuilt):
        return prebuilt

    src_dir = host_source_dir()
    if not os.path.isdir(src_dir):
        raise HostBuildError(f"找不到桌面分身宿主源码目录：{src_dir}")

    signature = source_signature(src_dir)
    exe_path = os.path.join(cache_dir(), f"DesktopCloneHost-{signature}.exe")
    if os.path.isfile(exe_path):
        return exe_path

    csc_path = find_csc()
    if csc_path is None:
        raise HostBuildError(
            "找不到 .NET Framework 编译器 csc.exe。\n"
            "Windows 10/11 一般自带 .NET Framework 4.x，若已被卸载请先安装后再使用桌面分身。"
        )

    _compile(csc_path, src_dir, exe_path)
    return exe_path


def _compile(csc_path: str, src_dir: str, exe_path: str) -> None:
    framework_dir = os.path.dirname(csc_path)
    temp_exe = f"{exe_path}.{os.getpid()}.tmp.exe"

    args = [
        csc_path,
        "-nologo",
        "-target:winexe",
        "-platform:anycpu",
        "-optimize+",
        f"-out:{temp_exe}",
    ]
    for assembly in FRAMEWORK_ASSEMBLIES:
        args.append(f"-r:{os.path.join(framework_dir, assembly)}")
    for name in HOST_SOURCES:
        args.append(os.path.join(src_dir, name))

    log.info(f"编译桌面分身宿主程序：{exe_path}")
    log_path = _build_log_path(exe_path)

    def _run(flags: int) -> int:
        # 输出重定向到文件而不是管道：某些受限环境（沙箱/无控制台）下创建管道会失败
        with open(log_path, "wb") as log_file:
            completed = subprocess.run(
                args,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                timeout=COMPILE_TIMEOUT_SECONDS,
                creationflags=flags,
            )
        return completed.returncode

    try:
        returncode = _run(CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        if returncode == CREATE_PROCESS_FAILED_CODE and sys.platform == "win32":
            # 0xC0000142：隐藏控制台方式启动编译器的环境不兼容，退回普通方式再试一次
            log.warning("隐藏窗口启动编译器失败，改为普通方式重试")
            returncode = _run(0)
    except subprocess.TimeoutExpired as e:
        _remove_quietly(temp_exe)
        _remove_quietly(log_path)
        raise HostBuildError("编译桌面分身宿主程序超时") from e
    except OSError as e:
        _remove_quietly(temp_exe)
        _remove_quietly(log_path)
        raise HostBuildError(f"无法启动编译器：{e}") from e

    if returncode != 0:
        _remove_quietly(temp_exe)
        output = _decode_console(_read_bytes(log_path))
        _remove_quietly(log_path)
        log.error(f"编译桌面分身宿主程序失败：\n{output}")
        raise HostBuildError(f"编译桌面分身宿主程序失败：\n{output.strip()[:2000]}")

    _remove_quietly(log_path)

    try:
        os.replace(temp_exe, exe_path)
    except OSError as e:
        _remove_quietly(temp_exe)
        raise HostBuildError(f"写入桌面分身宿主程序失败：{e}") from e

    log.info("桌面分身宿主程序编译完成")


def _build_log_path(exe_path: str) -> str:
    """编译器输出文件的路径。

    优先放到 exe 同目录（便于排查），目录不可写时退回系统临时目录，
    避免因为一个日志文件就把整个编译过程判为失败。
    """
    candidate = f"{exe_path}.{os.getpid()}.log"
    try:
        with open(candidate, "wb"):
            pass
        return candidate
    except OSError:
        pass

    try:
        path = os.path.join(
            tempfile.gettempdir(), f"desktop_clone_build_{os.getpid()}.log"
        )
        with open(path, "wb"):
            pass
        return path
    except OSError:
        return os.devnull


def _read_bytes(path: str) -> bytes | None:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _decode_console(raw: bytes | None) -> str:
    """csc.exe 的中文输出是 GBK。"""
    if not raw:
        return ""
    for encoding in ("gbk", "utf-8"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def _remove_quietly(path: str) -> None:
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass
