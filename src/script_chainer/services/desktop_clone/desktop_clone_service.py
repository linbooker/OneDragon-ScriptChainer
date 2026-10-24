"""桌面分身服务。

把「宿主进程（远程桌面控件）」和「界面」之间的事情串起来：

* 按界面给的窗口句柄启动/关闭宿主进程；
* 记录连接状态与子会话 id；
* 在分身里启动本脚本或指定程序；
* 把界面上的开关（置顶、只监视、缩放、静音、系统快捷键）同步给宿主。

服务本身不依赖任何界面控件，方便以后在命令行里复用。
"""

import os
import sys

from PySide6.QtCore import QObject, Signal

from one_dragon.utils import os_utils
from one_dragon.utils.log_utils import log
from script_chainer.config.desktop_clone_config import (
    DEFAULT_CHAIN_NAME,
    DEFAULT_TARGET_ARGUMENTS,
)
from script_chainer.services.desktop_clone import native_api
from script_chainer.services.desktop_clone.host_process import (
    STATE_CONNECTED,
    STATE_CONNECTING,
    STATE_RECONNECTING,
    DesktopCloneHost,
    state_text,
)

DEFAULT_DESKTOP_WIDTH = 1920
DEFAULT_DESKTOP_HEIGHT = 1080


class DesktopCloneService(QObject):
    """桌面分身的业务逻辑。"""

    state_changed = Signal()
    """连接状态 / 会话 id 变化。"""

    message = Signal(str)
    """需要提示给用户的信息。"""

    def __init__(self, ctx, parent=None):
        QObject.__init__(self, parent)
        self.ctx = ctx

        self._host: DesktopCloneHost | None = None
        self._parent_handle: int = 0
        self._state: int = 0
        self._session_id: int = 0
        self._last_error: str = ''
        self._environment_checked: bool = False
        self._applied_settings: tuple[bool, bool, bool, bool] | None = None
        self._embed_handle: int = 0
        self._embed_size: tuple[int, int] = (DEFAULT_DESKTOP_WIDTH, DEFAULT_DESKTOP_HEIGHT)

    # ---------- 状态 ----------

    @property
    def host(self) -> DesktopCloneHost | None:
        return self._host

    @property
    def state(self) -> int:
        return self._state

    @property
    def session_id(self) -> int:
        return self._session_id

    @property
    def last_error(self) -> str:
        return self._last_error

    @property
    def is_connected(self) -> bool:
        return self._state == STATE_CONNECTED

    @property
    def is_host_alive(self) -> bool:
        return self._host is not None and self._host.is_running()

    @property
    def state_text(self) -> str:
        text = state_text(self._state)
        if self._session_id:
            text = f'{text} · 会话 {self._session_id}'
        return text

    def describe_environment(self) -> str:
        return native_api.describe_environment()

    def collect_environment_issues(self) -> list[native_api.EnvironmentIssue]:
        return native_api.collect_environment_issues()

    # ---------- 宿主生命周期 ----------

    def start_host(self, parent_handle: int) -> bool:
        """启动桌面画面宿主进程。

        Args:
            parent_handle: 主窗口的 winId()。嵌进界面前，宿主的窗口先以它为所有者，
                免得刚启动时在任务栏里闪出一个独立窗口。

        Returns:
            是否成功启动宿主进程。
        """
        if parent_handle == 0:
            self._emit_message('桌面分身的窗口还没有准备好')
            return False

        if self.is_host_alive and self._parent_handle == parent_handle:
            return True

        # 句柄变了（比如页面重建）就重开一个宿主
        self.stop_host()

        issues = self.collect_environment_issues()
        fatal_issues = [issue for issue in issues if issue.fatal]
        if fatal_issues:
            for issue in fatal_issues:
                log.error(f'桌面分身环境检查失败：{issue.title} - {issue.message}')
            self._emit_message(fatal_issues[0].message)
            return False

        if not self._environment_checked:
            self._environment_checked = True
            for issue in issues:
                log.warning(f'桌面分身环境提示：{issue.title} - {issue.message}')

        if not native_api.is_child_sessions_enabled():
            log.info('子会话尚未开启，正在开启')
            if not native_api.enable_child_sessions():
                self._emit_message('开启 Windows 子会话失败，请确认程序以管理员身份运行')
                return False
            log.info('子会话已开启（部分系统需要注销后重新登录才生效）')

        host = DesktopCloneHost(self)
        host.ready.connect(self._on_host_ready)
        host.state_changed.connect(self._on_state_changed)
        host.session_changed.connect(self._on_session_changed)
        host.launched.connect(self._on_launched)
        host.message_received.connect(self._on_host_message)
        host.error_received.connect(self._on_host_error)
        host.process_exited.connect(self._on_host_exited)
        host.failed.connect(self._on_host_failed)

        if not host.start(parent_handle):
            return False

        self._host = host
        self._parent_handle = parent_handle
        self._last_error = ''
        self._applied_settings = None
        self._apply_settings_to_host()
        return True

    def set_embed_parent(self, handle: int) -> None:
        """指定画面要嵌进去的界面控件句柄（0 表示不嵌、用独立窗口）。"""
        self._embed_handle = int(handle)

    def set_embed_size(self, width: int, height: int) -> None:
        """指定嵌进去之后画面窗口的大小（控件客户区尺寸）。"""
        self._embed_size = (max(1, int(width)), max(1, int(height)))
        if self._host is not None:
            self._host.resize_desktop(*self._embed_size)

    def stop_host(self) -> None:
        if self._host is not None:
            self._host.stop()
            self._host.deleteLater()
            self._host = None
        self._parent_handle = 0
        self._state = 0
        self._session_id = 0
        self._applied_settings = None
        self.state_changed.emit()

    def shutdown(self) -> None:
        """程序退出时调用。"""
        self.stop_host()

    # ---------- 操作 ----------

    def connect_desktop(self) -> None:
        """连接（必要时先建）子会话桌面。"""
        if not self.is_host_alive:
            self._emit_message('桌面分身还没有启动')
            return

        if self._state in (STATE_CONNECTING, STATE_RECONNECTING):
            # 用户连点时不要重复发命令，否则宿主会在连接过程中反复重连
            return

        width, height = self._desktop_size()
        log.info(f'连接桌面分身：{width}x{height}')
        if self._embed_handle:
            self._host.embed_desktop(self._embed_handle, *self._embed_size)
        self._host.connect_desktop(width, height)

    def disconnect_desktop(self) -> None:
        if self.is_host_alive:
            self._host.disconnect_desktop()

    def logoff(self) -> None:
        """注销子会话（会关掉分身里运行的程序）。"""
        if self.is_host_alive:
            self._host.logoff()

    def send_shortcut(self, name: str) -> None:
        """向分身发送系统快捷键: wind(win+d) / wintab(win+tab)。"""
        if self.is_host_alive:
            self._host.send_shortcut(name)

    def change_resolution(self, width: int, height: int) -> None:
        """切换分身桌面分辨率：远程桌面要断开重连才生效。"""
        if not self.is_host_alive:
            return
        log.info(f'切换桌面分身分辨率：{width}x{height}')
        self._host.change_resolution(width, height)

    def set_input_enabled(self, enabled: bool) -> None:
        if self.is_host_alive:
            self._host.set_input_enabled(enabled)

    def launch(self, exe_path: str, arguments: str = '', work_directory: str = '') -> None:
        """在分身里启动程序。"""
        if not self.is_host_alive:
            self._emit_message('桌面分身还没有启动')
            return
        if not self._session_id:
            self._emit_message('分身会话还没有建立，请先连接桌面分身')
            return
        self._host.launch(exe_path, arguments, work_directory)

    def launch_self(self, chain_name: str | None = None) -> None:
        """把本程序（无界面运行）启动到分身里。"""
        exe_path, arguments, work_directory = self.self_launch_command(chain_name)
        log.info(f'在桌面分身里启动本程序：{exe_path} {arguments}')
        self.launch(exe_path, arguments, work_directory)

    def launch_configured_program(self) -> None:
        """在分身里启动配置里指定的程序。

        程序留空（或就是本程序）时启动本程序的无界面模式，
        这样「目标程序」默认那一栏不用改也能直接用。
        """
        config = self.config
        exe_path = os.path.expandvars((config.target_program or '').strip())

        if not exe_path or self._is_self_program(exe_path):
            self.launch_self()
            return

        work_directory = os.path.dirname(exe_path)
        arguments = (config.target_arguments or '').strip()
        if arguments == DEFAULT_TARGET_ARGUMENTS:
            # 用户换了目标程序但没改参数，别把本程序的参数硬塞给别人
            arguments = ''
        self.launch(exe_path, arguments, work_directory)

    def _is_self_program(self, exe_path: str) -> bool:
        try:
            return os.path.normcase(os.path.abspath(exe_path)) == os.path.normcase(
                os.path.abspath(sys.executable)
            )
        except (OSError, ValueError):
            return False

    def self_launch_command(self, chain_name: str | None = None) -> tuple[str, str, str]:
        """拼出「以无界面方式运行本程序」的命令行。

        Returns:
            (可执行文件, 参数, 工作目录)
        """
        chain = chain_name or self._default_chain_name()
        chain_args = f'--onedragon --chain {chain}'

        if os_utils.run_in_exe():
            exe_path = sys.executable
            work_directory = os.path.dirname(exe_path)
            return exe_path, chain_args, work_directory

        # 源码运行时用 -c 引导，保证分身里的进程能 import 到 one_dragon
        src_dir = os.path.join(os_utils.get_work_dir(), 'src')
        bootstrap = (
            'import sys;'
            f"sys.path.insert(0, r'{src_dir}');"
            'from script_chainer.win_exe.launcher import main;'
            'main()'
        )
        return sys.executable, f'-c "{bootstrap}" {chain_args}', os_utils.get_work_dir()

    # ---------- 设置 ----------

    @property
    def config(self):
        return self.ctx.desktop_clone_config

    def apply_settings(self) -> None:
        """界面上的开关变化后调用，把设置推给宿主。"""
        self._apply_settings_to_host()

    def _apply_settings_to_host(self) -> None:
        if not self.is_host_alive:
            return
        config = self.config
        snapshot = (
            bool(config.smart_sizing),
            bool(config.audio_muted),
            bool(config.send_system_shortcuts),
            not bool(config.watch_mode),
        )
        if snapshot == self._applied_settings:
            # 同一批设置反复推给宿主只会刷出重复提示，值没变就跳过
            return
        self._applied_settings = snapshot
        self._host.set_smart_sizing(snapshot[0])
        self._host.set_audio_muted(snapshot[1])
        self._host.set_system_shortcuts(snapshot[2])
        self._host.set_input_enabled(snapshot[3])

    def _desktop_size(self) -> tuple[int, int]:
        try:
            return self.config.resolution_size
        except (AttributeError, TypeError, ValueError):
            return DEFAULT_DESKTOP_WIDTH, DEFAULT_DESKTOP_HEIGHT

    def _default_chain_name(self) -> str:
        """优先用配置里选的脚本链，没选就用第一个。"""
        try:
            name = (self.config.chain_name or '').strip()
            if name:
                return name
        except AttributeError:
            pass

        try:
            configs = self.ctx.get_all_script_chain_config()
            if configs:
                return configs[0].module_name
        except (AttributeError, TypeError, IndexError):
            pass
        return DEFAULT_CHAIN_NAME

    # ---------- 宿主回调 ----------

    def _on_host_ready(self) -> None:
        log.info('桌面分身宿主已就绪')
        self._apply_settings_to_host()
        if self.config.auto_connect:
            self.connect_desktop()

    def _on_state_changed(self, state: int) -> None:
        if state == self._state:
            return
        log.info(f'桌面分身状态：{state_text(state)}')
        self._state = state
        self.state_changed.emit()

    def _on_session_changed(self, session_id: int) -> None:
        if session_id == self._session_id:
            return
        log.info(f'桌面分身的子会话 id：{session_id}')
        self._session_id = session_id
        self.state_changed.emit()

    def _on_launched(self, pid: int) -> None:
        log.info(f'已在桌面分身里启动进程：{pid}')
        self._emit_message(f'已在桌面分身里启动程序（PID {pid}）')

    def _on_host_error(self, text: str) -> None:
        self._last_error = text
        log.error(f'桌面分身出错：{text}')
        self._emit_message(text)
        self.state_changed.emit()

    def _on_host_failed(self, text: str) -> None:
        self._last_error = text
        log.error(f'桌面分身宿主出错：{text}')
        self._emit_message(text)
        self._state = 0
        self.state_changed.emit()

    def _on_host_exited(self, exit_code: int) -> None:
        self._state = 0
        if exit_code not in (0,) and not self._last_error:
            self._last_error = f'桌面分身宿主异常退出（退出码 {exit_code}）'
            log.warning(self._last_error)
        self.state_changed.emit()

    def _on_host_message(self, text: str) -> None:
        """宿主回报的进度信息：既写日志也显示到界面上。"""
        if text:
            log.info(f'桌面分身：{text}')
        self._emit_message(text)

    def _emit_message(self, text: str) -> None:
        if text:
            self.message.emit(text)
