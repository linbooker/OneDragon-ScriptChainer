"""桌面分身的小窗。

画面还是由宿主进程绘制，只是「舞台」从页面里换成了这个独立小窗。
小窗只提供两个操作：还原（放回页面）和置顶（切换窗口置顶）。

小窗无边框，所以拖动和缩放都得自己做：拖动按住标题栏，缩放拉四周/四角。
不用 WM_NCHITTEST 交给系统，因为无边框窗口没有 WS_THICKFRAME，系统不会接手。
"""

import ctypes

from PySide6.QtCore import QPoint, QRect, Qt, Signal
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

# 边框宽度：鼠标落在这一圈里就是缩放，不再拖动窗口
DRAG_BORDER = 6

_LEFT = 1
_RIGHT = 2
_TOP = 4
_BOTTOM = 8

try:  # 只有 Windows 上才有 user32，非 Windows 冒烟时直接跳过
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
except (AttributeError, OSError):
    _user32 = None

GWL_STYLE = -16
WS_THICKFRAME = 0x00040000


class DesktopCloneMiniWindow(QWidget):
    """无边框的小窗：拖标题栏移动、拉四周缩放，按钮只有「还原」和「置顶」。"""

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
        self.setMouseTracking(True)
        # 边框缩放是自己实现的，所以要收本窗口自己的鼠标事件
        self.installEventFilter(self)

        self._drag_offset: QPoint | None = None
        self._resize_edges = 0
        self._resize_start = QRect()
        self._resize_press = QPoint()

        layout = QVBoxLayout(self)
        # 四周留出 DRAG_BORDER，鼠标落在这一圈里就是缩放
        layout.setContentsMargins(DRAG_BORDER, DRAG_BORDER, DRAG_BORDER, DRAG_BORDER)
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

    def ensure_native_handle(self) -> int:
        """确保窗口和舞台的原生句柄都已经创建出来。"""
        self.winId()
        self._enable_native_frame()
        handle = self.host_widget.native_handle()
        self.host_widget.winId()
        return int(handle)

    def _enable_native_frame(self) -> None:
        """补上 WS_THICKFRAME。

        无边框窗口默认没有这个样式，但窗口上有原生子窗口（分身画面）时，
        某些操作（比如系统缩放、Win+方向键）需要它才认。
        """
        if _user32 is None:
            return
        try:
            hwnd = int(self.winId())
            style = _user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
            if not style & WS_THICKFRAME:
                _user32.SetWindowLongPtrW(hwnd, GWL_STYLE, style | WS_THICKFRAME)
        except (AttributeError, OSError, ValueError):
            pass

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
            self._enable_native_frame()

    # ---------- 拖动 / 缩放 ----------

    def _edges_at(self, pos: QPoint) -> int:
        """鼠标位置落在哪个边/角上。"""
        edges = 0
        if pos.x() <= DRAG_BORDER:
            edges |= _LEFT
        elif pos.x() >= self.width() - DRAG_BORDER:
            edges |= _RIGHT
        if pos.y() <= DRAG_BORDER:
            edges |= _TOP
        elif pos.y() >= self.height() - DRAG_BORDER:
            edges |= _BOTTOM
        return edges

    def _in_header_band(self, pos: QPoint) -> bool:
        """鼠标是否落在标题栏那一条上（窗口自身坐标）。"""
        top = DRAG_BORDER
        return top <= pos.y() <= top + _HEADER_HEIGHT

    def _update_cursor(self, edges: int) -> None:
        if edges in (_LEFT | _TOP, _RIGHT | _BOTTOM):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif edges in (_RIGHT | _TOP, _LEFT | _BOTTOM):
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif edges in (_LEFT, _RIGHT):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif edges in (_TOP, _BOTTOM):
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if not isinstance(event, QMouseEvent):
            return super().eventFilter(obj, event)

        if event.type() == QMouseEvent.Type.MouseButtonPress:
            if event.button() != Qt.MouseButton.LeftButton:
                return super().eventFilter(obj, event)
            if obj is self:
                edges = self._edges_at(event.position().toPoint())
                if edges:
                    self._begin_resize(edges, event.globalPosition().toPoint())
                    return True
                if not self._in_header_band(event.position().toPoint()):
                    return super().eventFilter(obj, event)
            # 标题栏：整条都可以拖
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            return True

        if event.type() == QMouseEvent.Type.MouseMove:
            if self._resize_edges:
                self._apply_resize(event.globalPosition().toPoint())
                return True
            if self._drag_offset is not None:
                self.move(event.globalPosition().toPoint() - self._drag_offset)
                return True
            if obj is self:
                self._update_cursor(self._edges_at(event.position().toPoint()))
            return super().eventFilter(obj, event)

        if event.type() == QMouseEvent.Type.MouseButtonRelease:
            if self._resize_edges:
                self._resize_edges = 0
                self.stage_resized.emit()
                return True
            if self._drag_offset is not None:
                self._drag_offset = None
                return True

        return super().eventFilter(obj, event)

    def _begin_resize(self, edges: int, global_pos: QPoint) -> None:
        self._resize_edges = edges
        self._resize_start = self.geometry()
        self._resize_press = global_pos
        self._update_cursor(edges)

    def _apply_resize(self, global_pos: QPoint) -> None:
        """按鼠标位移改窗口几何，左上角拖动时同时改位置。"""
        delta = global_pos - self._resize_press
        rect = QRect(self._resize_start)
        min_width = self.minimumWidth()
        min_height = self.minimumHeight()

        if self._resize_edges & _LEFT:
            width = max(min_width, rect.width() - delta.x())
            rect.setLeft(rect.right() - width + 1)
        elif self._resize_edges & _RIGHT:
            rect.setWidth(max(min_width, rect.width() + delta.x()))

        if self._resize_edges & _TOP:
            height = max(min_height, rect.height() - delta.y())
            rect.setTop(rect.bottom() - height + 1)
        elif self._resize_edges & _BOTTOM:
            rect.setHeight(max(min_height, rect.height() + delta.y()))

        self.setGeometry(rect)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.stage_resized.emit()
