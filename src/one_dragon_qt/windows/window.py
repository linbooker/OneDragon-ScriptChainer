import contextlib
import ctypes
from ctypes import wintypes

import win32api
import win32con
import win32gui
from PySide6.QtCore import (
    QAbstractNativeEventFilter,
    QEvent,
    QObject,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QColor, QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)
from qfluentwidgets import (
    FluentStyleSheet,
    InfoBar,
    InfoBarPosition,
    MSFluentWindow,
    NavigationBar,
    SplitTitleBar,
    isDarkTheme,
    qconfig,
)
from qfluentwidgets.common.animation import BackgroundAnimationWidget
from qfluentwidgets.components.widgets.frameless_window import FramelessWindow
from qfluentwidgets.window.stacked_widget import StackedWidget

from one_dragon_qt.utils.layout_utils import apply_shadow

try:
    _user32 = ctypes.WinDLL('user32', use_last_error=True)
except OSError:
    _user32 = None

_HWND_TOP = 0
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010


def _raise_native_child(widget_handle: int) -> None:
    """把一个原生子窗口提到它父窗口 z 序的最上面。

    注意：这里不能用 ``QWidget.raise_()``。``AA_DontCreateNativeWidgetSiblings``
    打开后，Qt 会把嵌了原生子窗口的祖先控件（``areaWidget``）也变成原生窗口，
    而 ``raise_()`` 对这种「原生兄弟窗口」是空操作（实测调完 z 序一点没变），
    只有直接调 ``SetWindowPos(HWND_TOP)`` 才真的能提上去。
    """
    if widget_handle == 0 or _user32 is None:
        return
    _user32.SetWindowPos(
        ctypes.c_void_p(widget_handle), ctypes.c_void_p(_HWND_TOP),
        0, 0, 0, 0,
        _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE,
    )


# 伪装父类 (替换 FluentWindowBase 初始化)
class PhosFluentWindowBase(BackgroundAnimationWidget, FramelessWindow):
    """Fluent window base class"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)


class _TitleBarWatcher(QObject):
    """盯着「有控件新拿了原生窗口 / 有子控件被重新显示」这件事，把标题栏压回最上层。

    原生子窗口是被插到父窗口 z 序最上面的。桌面分身的画面要 SetParent 进来，
    Qt 会把一路上的祖先控件（``areaWidget`` 等）都在**运行中**变成原生窗口，
    这些窗口建出来的时间比标题栏晚，就直接盖在标题栏上面，标题栏再也点不到。
    光在显示/切页时抬一次不够——变原生可能发生在任何时刻（点「启动画面」、
    嵌画面、开小窗），而且原生控件被 ``show()`` 时 Qt 也会把它提到最上面，
    所以这里改用事件驱动。
    """

    def __init__(self, window: QWidget):
        super().__init__(window)
        self._window = window
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        event_type = event.type()
        if event_type not in (QEvent.Type.WinIdChange, QEvent.Type.Show):
            return False
        if not isinstance(obj, QWidget):
            return False
        try:
            if obj is self._window.titleBar:
                return False
            if obj is not self._window and not self._window.isAncestorOf(obj):
                return False
            keep_on_top = getattr(self._window, '_keep_title_bar_on_top', None)
            if keep_on_top is not None:
                keep_on_top()
        except RuntimeError:
            # 窗口已经销毁、包装对象还活着的事件，直接忽略
            pass
        return False


class _NativeBorderFilter(QAbstractNativeEventFilter):
    """边框附近的命中测试交还给主窗口，保证无边框窗口还能从边上缩放。

    桌面分身的画面嵌进来后，Qt 会把 ``areaWidget`` 一路的祖先都变成原生窗口，
    它们铺满整个客户区，把无边框窗口自己的边框也盖住了。鼠标在边框那几像素上
    按下时命中的是这些子窗口，主窗口根本收不到 ``WM_NCHITTEST``，于是窗口既
    拖不了也缩不了。这里在子窗口做命中测试时直接回答 ``HTTRANSPARENT``，系统
    会改问同一线程里下层的窗口（也就是主窗口），``qframelesswindow`` 的边框
    判断就能正常跑起来。
    """

    # 与 qframelesswindow 的 BORDER_WIDTH 保持一致
    BORDER_WIDTH = 5

    def __init__(self, window: QWidget):
        super().__init__()
        self._window = window
        self._handle = 0

    def attach(self, handle: int) -> None:
        """主窗口的句柄变了（第一次显示、重建）就重新记一下。"""
        self._handle = handle

    def nativeEventFilter(self, event_type, message):
        if event_type not in (b'windows_generic_MSG', 'windows_generic_MSG'):
            return False, 0
        window_handle = self._handle
        if window_handle == 0 or _user32 is None:
            return False, 0
        try:
            msg = wintypes.MSG.from_address(int(message))
        except (TypeError, ValueError):
            return False, 0
        if msg.message != win32con.WM_NCHITTEST or not msg.hWnd:
            return False, 0

        child_handle = int(msg.hWnd)
        if child_handle == window_handle:
            # 主窗口自己那条消息交给 qframelesswindow 处理
            return False, 0

        try:
            window_state = self._window.windowState()
        except RuntimeError:
            # 窗口已经被销毁，过滤器还留在 QApplication 上
            self._handle = 0
            return False, 0
        if window_state & (Qt.WindowState.WindowMaximized | Qt.WindowState.WindowFullScreen):
            return False, 0

        try:
            if not win32gui.IsChild(window_handle, child_handle):
                return False, 0
        except win32gui.error:
            return False, 0

        try:
            x, y = win32gui.ScreenToClient(window_handle, win32api.GetCursorPos())
            left, top, right, bottom = win32gui.GetClientRect(window_handle)
        except win32gui.error:
            return False, 0

        border = self.BORDER_WIDTH
        on_border = (
            x < border or y < border
            or x > right - border or y > bottom - border
        )
        if not on_border:
            return False, 0

        # HTTRANSPARENT：这一下不算这个子窗口的，交给下层的窗口
        return True, win32con.HTTRANSPARENT


# 主窗口类 (继承自 MSFluentWindow )
class PhosWindow(MSFluentWindow, PhosFluentWindowBase):

    def __init__(self, parent=None):

        # 预配置
        self._isMicaEnabled = False

        self._lightBackgroundColor = QColor(248, 249, 252)
        self._darkBackgroundColor = QColor(39, 39, 39)

        # 父类初始化
        PhosFluentWindowBase.__init__(self, parent=parent)

        # 变量
        self.hBoxLayout = QHBoxLayout(self)
        self.stackedWidget = PhosStackedWidget(self)
        self.navigationInterface = NavigationBar(self)
        self.areaWidget = QWidget()
        self.areaWidget.setObjectName("areaWidget")
        self.areaLayout = QHBoxLayout(self.areaWidget)

        # 关系
        self.hBoxLayout.addWidget(self.navigationInterface)
        self.hBoxLayout.addWidget(self.areaWidget)
        self.areaLayout.addWidget(self.stackedWidget)
        self.setTitleBar(PhosTitleBar(self))

        # 标题栏要有自己的原生窗口，否则会被原生子窗口盖住、鼠标点不到，
        # 详见 _keep_title_bar_on_top()
        self.titleBar.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        # 任何后代控件在运行中变成原生窗口，都要把标题栏重新压回最上层
        self._title_bar_watcher = _TitleBarWatcher(self)
        self._title_raise_pending = False
        # 边框上的命中测试要交还给主窗口，否则原生子窗口会把缩放边吃掉，
        # 详见 _NativeBorderFilter
        self._native_border_filter = _NativeBorderFilter(self)
        app = QApplication.instance()
        if app is not None:
            app.installNativeEventFilter(self._native_border_filter)

        # 配置
        self.hBoxLayout.setSpacing(0)
        self.hBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.hBoxLayout.setStretchFactor(self.areaWidget, 1)
        self.areaLayout.setContentsMargins(11, 32, 11, 0)
        self.titleBar.raise_()
        self.titleBar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)

        # 样式
        FluentStyleSheet.FLUENT_WINDOW.apply(self.stackedWidget)

        # 函数
        qconfig.themeChangedFinished.connect(self._onThemeChangedFinished)

    # 根据主题获取对应的背景色
    def _normalBackgroundColor(self):
        if isDarkTheme():
            return self._darkBackgroundColor
        else:
            return self._lightBackgroundColor

    def showEvent(self, event):
        super().showEvent(event)
        self._native_border_filter.attach(int(self.winId()))
        self._keep_title_bar_on_top()

    def _keep_title_bar_on_top(self) -> None:
        """把标题栏压在最上层，并保证它自己有独立的原生窗口。

        桌面分身的画面是另一个进程的原生子窗口，Qt 会连带把它所有的祖先都变成
        原生窗口；``areaWidget`` 的原生窗口铺满客户区（连标题栏那一条也盖住）。
        标题栏如果还是不需要句柄的普通控件，鼠标点下去命中的就是 ``areaWidget``
        的原生窗口，标题栏根本收不到按下事件，窗口也就拖不动了。

        原生化是 Qt 在内部异步做的（切页、嵌画面、开小窗都可能触发），所以这
        里除了当场提一次，还在事件循环空闲时再补一次。
        """
        title_bar = self.titleBar
        if title_bar is None:
            return
        # 窗口句柄可能被重建（改窗口标志、切原生），顺手把边框过滤器指回新句柄
        window_handle = int(self.internalWinId())
        if window_handle:
            self._native_border_filter.attach(window_handle)
        if not title_bar.testAttribute(Qt.WidgetAttribute.WA_NativeWindow):
            title_bar.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        title_bar.raise_()
        _raise_native_child(int(title_bar.winId()))
        self._schedule_title_bar_raise()

    def _schedule_title_bar_raise(self) -> None:
        """事件循环空闲时再提一次标题栏，兜住 Qt 延后创建原生窗口的情况。
        """
        if self._title_raise_pending:
            return
        self._title_raise_pending = True

        def raise_again() -> None:
            self._title_raise_pending = False
            title_bar = self.titleBar
            if title_bar is None:
                return
            with contextlib.suppress(RuntimeError):
                _raise_native_child(int(title_bar.winId()))

        QTimer.singleShot(0, raise_again)

    # 调整标题栏位置
    def resizeEvent(self, e):
        self.titleBar.move(88, 0)
        self.titleBar.resize(self.width() - 88, self.titleBar.height())
        self._keep_title_bar_on_top()


class PhosTitleBar(SplitTitleBar):
    """One Dragon 自定义标题栏

    跳过 SplitTitleBar.__init__ 以完全自定义布局（图标、标题、版本号、问题反馈按钮）。
    注意: 若 SplitTitleBar/TitleBar 新增属性，需在此处手动同步。
    """

    def __init__(self, parent=None):
        # 跳过 SplitTitleBar.__init__，直接调用 TitleBar.__init__
        super(SplitTitleBar, self).__init__(parent)

        # 设置标题栏的固定高度
        self.setFixedHeight(32)

        # 窗口图标
        self.iconLabel = QLabel(self)
        self.iconLabel.setFixedSize(18, 18)
        self.hBoxLayout.insertSpacing(0, 0)
        self.hBoxLayout.insertWidget(
            1, self.iconLabel, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom
        )
        self.window().windowIconChanged.connect(self.setIcon)

        # 窗口标题
        self.titleLabel = QLabel(self)
        self.titleLabel.setObjectName("titleLabel")
        self.hBoxLayout.insertWidget(
            2, self.titleLabel, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom
        )
        self.window().windowTitleChanged.connect(self.setTitle)

        # 版本号与问题反馈按钮
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 0, 0, 0)

        self.launcherVersionButton = QPushButton("ⓘ 启动器版本 未知")
        self.launcherVersionButton.setObjectName("launcherVersionButton")
        self.launcherVersionButton.clicked.connect(lambda: self.copy_version(self.launcher_version))
        self.launcherVersionButton.setVisible(False)
        btn_layout.addWidget(
            self.launcherVersionButton,
            0,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
        )

        self.codeVersionButton = QPushButton("ⓘ 代码版本 未知")
        self.codeVersionButton.setObjectName("codeVersionButton")
        self.codeVersionButton.clicked.connect(lambda: self.copy_version(self.code_version))
        self.codeVersionButton.setVisible(False)
        btn_layout.addWidget(
            self.codeVersionButton,
            0,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
        )

        self.questionButton = QPushButton("ⓘ 问题反馈")
        self.questionButton.setObjectName("questionButton")
        self.questionButton.clicked.connect(self.open_github)
        btn_layout.addWidget(
            self.questionButton,
            0,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
        )

        self.hBoxLayout.insertLayout(4, btn_layout)

        self.issue_url: str = ""
        self.launcher_version: str = ""
        self.code_version: str = ""

        # 首页模式下需要添加阴影的控件列表
        self._home_shadow_targets: list[QWidget] = [
            self.titleLabel,
            self.launcherVersionButton,
            self.codeVersionButton,
            self.questionButton,
            self.minBtn,
            self.maxBtn,
            self.closeBtn,
        ]
        self._is_home_mode: bool = False

        self.setProperty("homeMode", "false")

    def setIcon(self, icon: QIcon):
        self.iconLabel.setPixmap(icon.pixmap(18, 18))

    def setTitle(self, title: str):
        self.titleLabel.setText(title)

    def setLauncherVersion(self, version: str) -> None:
        """
        设置启动器版本号 会更新UI
        @param version: 版本号
        @return:
        """
        self.launcher_version = version
        self.launcherVersionButton.setText(f"ⓘ 启动器版本 {version}")
        if version:
            self.launcherVersionButton.setVisible(True)

    def setCodeVersion(self, version: str) -> None:
        """
        设置代码版本号 会更新UI
        @param version: 版本号
        @return:
        """
        self.code_version = version
        self.codeVersionButton.setText(f"ⓘ 代码版本 {version}")
        if version:
            self.codeVersionButton.setVisible(True)

    def setInstallerVersion(self, version: str) -> None:
        """
        设置安装器版本号 会更新UI
        @param version: 版本号
        @return:
        """
        self.launcher_version = version
        self.launcherVersionButton.setText(f"ⓘ 安装器版本 {version}")
        if version:
            self.launcherVersionButton.setVisible(True)

    def setVersion(self, version: str) -> None:
        """
        设置程序版本号 会更新UI
        @param version: 版本号
        @return:
        """
        self.launcher_version = version
        self.launcherVersionButton.setText(f"ⓘ 程序版本 {version}")
        if version:
            self.launcherVersionButton.setVisible(True)

    def set_home_mode(self, enable: bool) -> None:
        """首页模式切换：通过动态属性 homeMode 选择 QSS 规则，配合阴影效果。"""
        if enable:
            if not self._is_home_mode:
                self._is_home_mode = True
            self.setProperty("homeMode", "true")
            self.setStyleSheet(self.styleSheet())
            self._apply_home_shadows()
        else:
            if not self._is_home_mode:
                return
            self._is_home_mode = False
            self._clear_home_shadows()
            self.setProperty("homeMode", "false")
            self.setStyleSheet(self.styleSheet())

    def _apply_home_shadows(self) -> None:
        """给首页标题栏的文字和按钮补硬阴影，提升海报背景上的可读性。"""
        for widget in self._home_shadow_targets:
            apply_shadow(widget, offset_x=1, offset_y=1)

    def _clear_home_shadows(self) -> None:
        """清除首页标题栏的阴影效果。"""
        for widget in self._home_shadow_targets:
            widget.setGraphicsEffect(None)

    # 定义打开GitHub网页的函数
    def open_github(self):
        url = QUrl(self.issue_url)
        QDesktopServices.openUrl(url)

    def copy_version(self, text: str):
        """
        将版本号复制到粘贴板
        @return:
        """
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        InfoBar.success(
            title="已复制版本号",
            content="",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP_RIGHT,
            duration=2000,
            parent=self.window(),
        ).setCustomBackgroundColor("white", "#202020")


class PhosStackedWidget(StackedWidget):
    """Stacked widget"""

    currentChanged = Signal(int)
    beforeCurrentChanged = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent=parent)

    def setCurrentWidget(self, widget, popOut=True):
        if isinstance(widget, QAbstractScrollArea):
            widget.verticalScrollBar().setValue(0)
        old_idx = self.view.currentIndex()
        new_idx = self.view.indexOf(widget)
        if old_idx != new_idx:
            self.beforeCurrentChanged.emit(old_idx, new_idx)
        self.view.setCurrentWidget(widget, duration=0)
