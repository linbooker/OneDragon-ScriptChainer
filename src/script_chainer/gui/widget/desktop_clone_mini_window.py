"""桌面分身的小窗。

画面还是由宿主进程绘制，只是「舞台」从页面里换成了这个独立小窗。
小窗只提供两个操作：还原（放回页面）和置顶（切换窗口置顶）。
"""

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from script_chainer.gui.widget.desktop_clone_host_widget import (
    DesktopCloneHostWidget,
)

MINI_TITLE = "桌面分身"
MINI_DEFAULT_WIDTH = 640
MINI_DEFAULT_HEIGHT = 400
MINI_MIN_WIDTH = 420
MINI_MIN_HEIGHT = 280

_HEADER_HEIGHT = 30
_ACCENT = "#4f6ef7"


class DesktopCloneMiniWindow(QWidget):
    """无边框的小窗：拖动标题栏移动，按钮只有「还原」和「置顶」。"""

    restore_requested = Signal()
    """用户点了「还原」。"""

    pin_changed = Signal(bool)
    """置顶状态变化。"""

    stage_resized = Signal()
    """画面区域尺寸变化。"""

    def __init__(self, parent=None):
        QWidget.__init__(self, parent=parent)
        self.setObjectName("desktop_clone_mini_window")
        self.setWindowTitle(MINI_TITLE)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setStyleSheet(
            "#desktop_clone_mini_window { background-color: #16161c; }"
            "#desktop_clone_mini_header { background-color: #1f1f27; }"
            "QLabel { color: #d8d8e0; font-size: 12px; }"
        )
        self.resize(MINI_DEFAULT_WIDTH, MINI_DEFAULT_HEIGHT)
        self.setMinimumSize(MINI_MIN_WIDTH, MINI_MIN_HEIGHT)

        self._drag_offset: QPoint | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_header())

        self.host_widget = DesktopCloneHostWidget(self)
        self.host_widget.stage_resized.connect(self.stage_resized.emit)
        layout.addWidget(self.host_widget, 1)

    # ---------- 头部 ----------

    def _build_header(self) -> QWidget:
        header = QWidget(self)
        header.setObjectName("desktop_clone_mini_header")
        header.setFixedHeight(_HEADER_HEIGHT)
        row = QHBoxLayout(header)
        row.setContentsMargins(10, 0, 6, 0)
        row.setSpacing(6)

        self.title_label = QLabel(MINI_TITLE, header)
        row.addWidget(self.title_label)
        row.addStretch(1)

        self.pin_btn = QPushButton("置顶", header)
        self.pin_btn.setCheckable(True)
        self.pin_btn.setChecked(True)
        self.pin_btn.setFixedHeight(22)
        self.pin_btn.setStyleSheet(
            "QPushButton { border: 1px solid #3a3a48; border-radius: 4px;"
            " color: #c8c8d4; padding: 0 8px; background: transparent; }"
            f"QPushButton:checked {{ border-color: {_ACCENT}; color: #ffffff;"
            f" background: {_ACCENT}; }}"
        )
        self.pin_btn.clicked.connect(self._on_pin_clicked)
        row.addWidget(self.pin_btn)

        self.restore_btn = QPushButton("还原", header)
        self.restore_btn.setFixedHeight(22)
        self.restore_btn.setStyleSheet(
            "QPushButton { border: 1px solid #3a3a48; border-radius: 4px;"
            " color: #c8c8d4; padding: 0 8px; background: transparent; }"
            "QPushButton:hover { border-color: #6a6a80; color: #ffffff; }"
        )
        self.restore_btn.clicked.connect(self.restore_requested.emit)
        row.addWidget(self.restore_btn)

        header.installEventFilter(self)
        # 标题也算拖动区，不然按住文字拖不动窗口
        self.title_label.installEventFilter(self)
        return header

    # ---------- 画面 ----------

    def native_handle(self) -> int:
        """宿主窗口要挂到的舞台句柄。"""
        return self.host_widget.native_handle()

    def stage_size(self) -> tuple[int, int]:
        """画面尺寸（物理像素）。"""
        return self.host_widget.stage_size()

    def set_placeholder_text(self, text: str) -> None:
        self.host_widget.set_placeholder_text(text)

    def show_placeholder(self, visible: bool) -> None:
        self.host_widget.show_placeholder(visible)

    # ---------- 置顶 ----------

    def is_pinned(self) -> bool:
        return self.pin_btn.isChecked()

    def set_pinned(self, pinned: bool) -> None:
        if self.pin_btn.isChecked() != pinned:
            self.pin_btn.setChecked(pinned)
        self._apply_pinned(pinned)

    def _on_pin_clicked(self) -> None:
        pinned = self.pin_btn.isChecked()
        self._apply_pinned(pinned)
        self.pin_changed.emit(pinned)

    def _apply_pinned(self, pinned: bool) -> None:
        """切换置顶要重建窗口标志，顺手把位置和大小保住。"""
        flags = self.windowFlags()
        if pinned:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        geometry = self.geometry()
        visible = self.isVisible()
        self.setWindowFlags(flags)
        self.setGeometry(geometry)
        if visible:
            self.show()
            self.raise_()

    # ---------- 拖动 ----------

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if isinstance(event, QMouseEvent):
            if event.type() == QMouseEvent.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.LeftButton:
                    self._drag_offset = (
                        event.globalPosition().toPoint()
                        - self.frameGeometry().topLeft()
                    )
                    return True
            elif event.type() == QMouseEvent.Type.MouseMove:
                if self._drag_offset is not None:
                    self.move(event.globalPosition().toPoint() - self._drag_offset)
                    return True
            elif event.type() == QMouseEvent.Type.MouseButtonRelease:
                if self._drag_offset is not None:
                    self._drag_offset = None
                    return True
        return super().eventFilter(obj, event)
