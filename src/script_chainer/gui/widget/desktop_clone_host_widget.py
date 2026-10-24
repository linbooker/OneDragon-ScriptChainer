"""用来承载桌面分身画面的控件。

真正的画面由 C# 宿主进程里的远程桌面控件绘制，宿主窗口会被 SetParent
挂到本控件的 `stage` 上，于是画面就是界面里的一块。这里只负责准备
一块黑色的舞台并按 16:9 居中摆放，尺寸变化时通知外面同步。
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QWidget

STAGE_ASPECT_WIDTH = 16
STAGE_ASPECT_HEIGHT = 9
MIN_STAGE_WIDTH = 320
MIN_STAGE_HEIGHT = 180


class DesktopCloneHostWidget(QWidget):
    """黑色背景 + 居中的画面舞台。"""

    stage_resized = Signal()

    def __init__(self, parent=None):
        QWidget.__init__(self, parent=parent)
        self.setObjectName('desktop_clone_host_widget')
        self.setStyleSheet(
            '#desktop_clone_host_widget { background-color: #101014; }'
            '#desktop_clone_host_stage { background-color: #000000; }'
        )
        self.setMinimumHeight(240)

        self._stage = QWidget(self)
        self._stage.setObjectName('desktop_clone_host_stage')
        # 宿主需要真正的 HWND 才能 SetParent 进来，所以强制这个控件原生化
        self._stage.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)

        self._placeholder = QLabel('桌面分身尚未连接', self)
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet('color: #8a8a8a; font-size: 14px; background: transparent;')

        # 舞台的位置和大小由 _layout_stage 手动计算，不用布局管理器
        self._layout_stage()

    # ---------- 句柄 ----------

    def native_handle(self) -> int:
        """给 C# 宿主用的窗口句柄（舞台自身的 HWND，用于推算屏幕位置）。"""
        return int(self._stage.winId())

    def stage_size(self) -> tuple[int, int]:
        return self._stage.width(), self._stage.height()

    def stage_global_rect(self) -> tuple[int, int, int, int]:
        """舞台区域在屏幕上的 (x, y, 宽, 高)，用来摆放宿主窗口。

        宿主进程声明了按监视器 DPI 感知，用的是真实像素，所以这里把 Qt 的
        逻辑坐标乘上缩放因子，避免高 DPI 下窗口偏大、画面跑到右下角。
        """
        ratio = self.devicePixelRatioF()
        top_left = self._stage.mapToGlobal(self._stage.rect().topLeft())
        width = int(round(self._stage.width() * ratio))
        height = int(round(self._stage.height() * ratio))
        return int(round(top_left.x() * ratio)), int(round(top_left.y() * ratio)), width, height

    # ---------- 占位文字 ----------

    def set_placeholder_text(self, text: str) -> None:
        self._placeholder.setText(text)

    def show_placeholder(self, visible: bool) -> None:
        self._placeholder.setVisible(visible)
        if visible:
            self._placeholder.raise_()

    # ---------- 布局 ----------

    def resizeEvent(self, event) -> None:
        QWidget.resizeEvent(self, event)
        self._layout_stage()
        self.stage_resized.emit()

    def _layout_stage(self) -> None:
        available_width = max(self.width(), MIN_STAGE_WIDTH)
        available_height = max(self.height(), MIN_STAGE_HEIGHT)

        width = available_width
        height = int(width * STAGE_ASPECT_HEIGHT / STAGE_ASPECT_WIDTH)
        if height > available_height:
            height = available_height
            width = int(height * STAGE_ASPECT_WIDTH / STAGE_ASPECT_HEIGHT)

        width = max(width, MIN_STAGE_WIDTH)
        height = max(height, MIN_STAGE_HEIGHT)

        left = (self.width() - width) // 2
        top = (self.height() - height) // 2
        self._stage.setGeometry(left, top, width, height)
        self._placeholder.setGeometry(0, 0, self.width(), self.height())
