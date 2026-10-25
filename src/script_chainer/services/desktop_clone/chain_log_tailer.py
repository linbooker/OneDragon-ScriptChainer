"""把桌面分身里启动的千机链日志转发回主界面。

分身在另一个 Windows 会话里，父进程拿不到它的 stdout，但程序自己会把日志写进
工作目录的 ``.log/``；这里盯着那个目录，按偏移把新增的整行读出来，相当于把
分身里的日志窗口搬到主界面上。

转发本身必须是「只读」的：读到的一行绝不写回任何被盯着的日志文件。否则
「读一行 → 写回文件 → 再读到这一行」会无限自我复制，日志框会被刷爆。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from one_dragon.utils.log_utils import log

POLL_INTERVAL_MS = 500
"""轮询间隔：日志要跟着滚，半秒一次够用。"""

MAX_READ_BYTES = 64 * 1024
"""单次最多读这么多，避免第一次就把一个大文件整个吞进来。"""

LOG_DIR_NAME = ".log"
"""千机链把自己的日志写在工作目录的 ``.log/`` 下面。"""

# 只盯「分身里那个千机链」自己写的运行日志。
# 不盯 ``log.txt``：那是主界面这个进程的框架日志，转发它的行等于把主界面自己的
# 日志再念一遍；万一哪天界面又把日志写回去，就是死循环。
RUNNER_LOG_NAME = "script_chainer_runner.log"
TAILED_NAMES = (RUNNER_LOG_NAME,)


class ChainLogTailer(QObject):
    """盯住一个日志目录，把新增的整行发出来。"""

    line_received = Signal(str)
    """读到一行新日志。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll)

        self._log_dir: Path | None = None
        self._offsets: dict[str, int] = {}
        self._pending: dict[str, bytes] = {}

    def is_tailing(self) -> bool:
        return self._timer.isActive()

    def prime(self, log_dir: str) -> None:
        """记下「启动之前」各日志文件的大小，避免把上一次运行的内容当新的读出来。

        要在真正启动分身里的程序之前调用：先记位置，再启动，然后 ``start()``，
        这样只有新写进去的行会被转发。
        """
        self._timer.stop()
        self._pending.clear()
        self._log_dir = Path(log_dir) if log_dir else None
        self._offsets = {}
        if self._log_dir is None:
            return
        # 还不存在的文件先记 0：它一旦出现就是这次运行新建的，从头读才对
        for name in TAILED_NAMES:
            self._record(Path(self._log_dir) / name)

    def start(self, log_dir: str | None = None) -> None:
        """开始盯目录里的日志文件（已经在写的老内容不重放）。"""
        if log_dir:
            path = Path(log_dir)
            if path != self._log_dir:
                # 换了目录：老偏移没意义了，按当前大小重新记
                self._timer.stop()
                self._pending.clear()
                self._log_dir = path
                self._offsets = {}
                for name in TAILED_NAMES:
                    self._record(self._log_dir / name)
        if self._log_dir is None:
            return
        if not self._log_dir.is_dir():
            # 目录可能还没建（程序刚启动）：照样轮询，等它出现再读，
            # 省得「第一次启动什么都收不到」
            log.info(f"分身日志目录还没出现，等它创建：{self._log_dir}")
        # prime() 没调过时，补齐缺失文件的偏移
        for name in TAILED_NAMES:
            file = self._log_dir / name
            if str(file) not in self._offsets:
                self._record(file)
        log.info(f"开始转发分身的日志：{self._log_dir}")
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._log_dir = None
        self._offsets.clear()
        self._pending.clear()

    def _record(self, file: Path) -> None:
        """把偏移设成文件当前大小（不存在就是 0）。"""
        try:
            self._offsets[str(file)] = file.stat().st_size
        except OSError:
            self._offsets[str(file)] = 0

    def _candidate_files(self) -> list[Path]:
        if self._log_dir is None:
            return []
        files: list[Path] = []
        for name in TAILED_NAMES:
            candidate = self._log_dir / name
            if candidate.is_file():
                files.append(candidate)
        return files

    def _poll(self) -> None:
        for file in self._candidate_files():
            self._read_new_lines(file)

    def _read_new_lines(self, file: Path) -> None:
        key = str(file)
        offset = self._offsets.get(key, 0)
        try:
            size = file.stat().st_size
            if size < offset:  # 文件被截断或轮转了，从头来
                offset = 0
                self._pending.pop(key, None)
            if size == offset:
                self._offsets[key] = offset
                return
            with file.open("rb") as handle:
                handle.seek(offset)
                data = handle.read(min(MAX_READ_BYTES, size - offset))
        except OSError:
            return

        self._offsets[key] = offset + len(data)
        buffer = self._pending.get(key, b"") + data
        *lines, rest = buffer.split(b"\n")
        self._pending[key] = rest

        for raw in lines:
            text = raw.decode("utf-8", "replace").rstrip("\r")
            if text.strip():
                self.line_received.emit(text)


def chain_log_dir(work_directory: str) -> str:
    """算出被启动程序的日志目录。

    Args:
        work_directory: 启动程序时的工作目录；留空表示就是本程序所在目录。

    Returns:
        该工作目录下的 ``.log`` 路径。
    """
    base = (work_directory or "").strip()
    if not base:
        base = os.path.dirname(sys.executable)
    return os.path.join(base, LOG_DIR_NAME)
