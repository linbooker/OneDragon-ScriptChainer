"""桌面分身宿主进程（DesktopCloneHost.exe）的 Python 封装。

宿主是一个 C# WinForms 程序：内嵌远程桌面 ActiveX 控件连到本机子会话，
并把画面挂在界面传入的 native 窗口句柄上。双方通过标准输入输出的单行协议通信：

    宿主 -> Python: ready / state N / session N / launched PID / message BASE64 /
                    error BASE64 / bye
    Python -> 宿主: connect W H / disconnect / logoff / launch EXE ARGS DIR （base64）/
                    shortcut wind|wintab / smartsizing 0|1 / audiomute 0|1 /
                    syskeys 0|1 / input 0|1 / status / quit
"""

import base64

from PySide6.QtCore import QObject, QProcess, Signal

from one_dragon.utils.log_utils import log
from script_chainer.services.desktop_clone.host_builder import (
    HostBuildError,
    resolve_host_exe,
)

STATE_DISCONNECTED = 0
STATE_CONNECTED = 1
STATE_CONNECTING = 2
STATE_DISCONNECTING = 3
STATE_RECONNECTING = 4

STATE_TEXT = {
    STATE_DISCONNECTED: "未连接",
    STATE_CONNECTED: "已连接",
    STATE_CONNECTING: "正在连接",
    STATE_DISCONNECTING: "正在断开",
    STATE_RECONNECTING: "正在重连",
}

STOP_WAIT_MILLIS = 3000


def state_text(state: int) -> str:
    return STATE_TEXT.get(state, f"未知状态({state})")


class DesktopCloneHost(QObject):
    """管理宿主进程的生命周期与消息收发。"""

    ready = Signal()
    state_changed = Signal(int)
    session_changed = Signal(int)
    launched = Signal(int)
    message_received = Signal(str)
    error_received = Signal(str)
    process_exited = Signal(int)
    failed = Signal(str)

    def __init__(self, parent=None):
        QObject.__init__(self, parent)
        self._process: QProcess | None = None
        self._stdout_buffer = bytearray()
        self.window_handle = 0

    def is_running(self) -> bool:
        return (
            self._process is not None
            and self._process.state() != QProcess.ProcessState.NotRunning
        )

    def start(self, parent_handle: int) -> bool:
        """启动宿主进程并让它把画面挂到 parent_handle 上。"""
        if self.is_running():
            return True

        try:
            exe_path = resolve_host_exe()
        except HostBuildError as e:
            log.error(f"准备桌面分身宿主程序失败：{e}")
            self.failed.emit(str(e))
            return False

        process = QProcess(self)
        process.setProgram(exe_path)
        process.setArguments(["--parent", str(parent_handle)])
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        process.readyReadStandardOutput.connect(self._on_stdout)
        process.readyReadStandardError.connect(self._on_stderr)
        process.finished.connect(self._on_finished)
        process.errorOccurred.connect(self._on_error_occurred)

        self._process = process
        self._stdout_buffer.clear()
        log.info(f"启动桌面分身宿主：{exe_path} --parent {parent_handle}")
        process.start()
        if not process.waitForStarted(STOP_WAIT_MILLIS):
            log.error("桌面分身宿主启动失败")
            self.failed.emit("桌面分身宿主启动失败")
            self._process = None
            return False
        return True

    def send(self, command: str) -> None:
        """向宿主写一行命令。宿主没起来时静默丢弃，避免界面操作报错。"""
        process = self._process
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            return

        try:
            written = process.write((command + "\n").encode("utf-8"))
        except RuntimeError as e:
            # 底层进程对象已经被销毁（界面关掉了）
            log.warning(f"向桌面分身宿主发送命令失败：{e}")
            return

        if written < 0:
            log.warning(f"向桌面分身宿主发送命令失败：{process.errorString()}")
            return
        process.waitForBytesWritten(1000)

    def connect_desktop(self, width: int, height: int) -> None:
        self.send(f"connect {width} {height}")

    def embed_desktop(self, parent_handle: int, width: int, height: int) -> None:
        """把画面嵌进界面控件（parent_handle 是该控件的 HWND）。

        必须赶在 connect 之前发：远程桌面控件连上之后才嵌，
        窗口大小变化会让画面重新协商、闪一下。
        """
        self.send(f"embed {int(parent_handle)} {max(1, width)} {max(1, height)}")

    def resize_desktop(self, width: int, height: int) -> None:
        """界面控件尺寸变化后同步画面窗口大小。"""
        self.send(f"resize {max(1, width)} {max(1, height)}")

    def set_embedded_visible(self, visible: bool) -> None:
        """画面所在的页面被切走时把嵌入窗口藏起来。"""
        self.send(f"visible {_flag(visible)}")

    def change_resolution(self, width: int, height: int) -> None:
        """改分身桌面分辨率（宿主会断开后用新尺寸重连）。"""
        self.send(f"resolution {int(width)} {int(height)}")

    def disconnect_desktop(self) -> None:
        self.send("disconnect")

    def logoff(self) -> None:
        self.send("logoff")

    def launch(
        self, exe_path: str, arguments: str = "", work_directory: str = ""
    ) -> None:
        parts = [
            "launch",
            _encode(exe_path),
            _encode(arguments),
            _encode(work_directory),
        ]
        self.send(" ".join(parts))

    def send_shortcut(self, name: str) -> None:
        self.send(f"shortcut {name}")

    def set_smart_sizing(self, enabled: bool) -> None:
        self.send(f"smartsizing {_flag(enabled)}")

    def set_audio_muted(self, muted: bool) -> None:
        self.send(f"audiomute {_flag(muted)}")

    def set_system_shortcuts(self, enabled: bool) -> None:
        self.send(f"syskeys {_flag(enabled)}")

    def set_input_enabled(self, enabled: bool) -> None:
        self.send(f"input {_flag(enabled)}")

    def request_status(self) -> None:
        self.send("status")

    def stop(self) -> None:
        """先礼后兵地关掉宿主。"""
        process = self._process
        # 先摘掉引用，退出过程中收到的输出/退出信号不再影响新一轮宿主
        self._process = None
        if process is None:
            return

        try:
            if process.state() != QProcess.ProcessState.NotRunning:
                process.write(b"quit\n")
                if not process.waitForFinished(STOP_WAIT_MILLIS):
                    log.warning("桌面分身宿主未响应退出，强制结束")
                    process.kill()
                    process.waitForFinished(1000)
        except RuntimeError:
            # 底层进程对象已经被销毁
            pass
        finally:
            process.deleteLater()

    def _on_stdout(self) -> None:
        if self._process is None:
            return
        self._stdout_buffer.extend(bytes(self._process.readAllStandardOutput()))
        while True:
            index = self._stdout_buffer.find(b"\n")
            if index < 0:
                break
            raw = bytes(self._stdout_buffer[:index])
            del self._stdout_buffer[: index + 1]
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                self._handle_line(line)

    def _handle_line(self, line: str) -> None:
        verb, _, payload = line.partition(" ")
        verb = verb.strip().lower()
        payload = payload.strip()

        try:
            if verb == "ready":
                try:
                    self.window_handle = int(payload)
                except (TypeError, ValueError):
                    self.window_handle = 0
                self.ready.emit()
            elif verb == "state":
                self.state_changed.emit(int(payload))
            elif verb == "session":
                self.session_changed.emit(int(payload))
            elif verb == "launched":
                self.launched.emit(int(payload))
            elif verb == "message":
                self.message_received.emit(_decode(payload))
            elif verb == "error":
                self.error_received.emit(_decode(payload))
            elif verb == "bye":
                log.info("桌面分身宿主已退出")
            else:
                log.debug(f"桌面分身宿主输出：{line}")
        except ValueError:
            log.warning(f"无法解析桌面分身宿主输出：{line}")

    def _on_stderr(self) -> None:
        if self._process is None:
            return
        text = (
            bytes(self._process.readAllStandardError())
            .decode("utf-8", errors="replace")
            .strip()
        )
        if text:
            log.debug(f"桌面分身宿主 stderr：{text}")

    def _on_finished(self, exit_code: int, _status) -> None:
        log.info(f"桌面分身宿主进程结束，退出码 {exit_code}")
        self._process = None
        self.process_exited.emit(exit_code)

    def _on_error_occurred(self, error) -> None:
        if self._process is None:
            return
        self.failed.emit(f"桌面分身宿主运行出错：{self._process.errorString()}")
        log.error(f"桌面分身宿主运行出错：{error} {self._process.errorString()}")


def _encode(text: str) -> str:
    """协议里的文本参数统一 base64，避免空格/中文/引号出问题。"""
    return base64.b64encode((text or "").encode("utf-8")).decode("ascii")


def _decode(text: str) -> str:
    if not text:
        return ""
    try:
        return base64.b64decode(text.encode("ascii")).decode("utf-8", errors="replace")
    except (ValueError, UnicodeDecodeError):
        return text


def _flag(value: bool) -> str:
    return "1" if value else "0"
