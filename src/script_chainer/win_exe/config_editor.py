import time

import win32con
import win32gui
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QMessageBox
from qfluentwidgets import (
    FluentIcon,
    NavigationItemPosition,
    Theme,
    setTheme,
)

from one_dragon.utils.log_utils import log
from one_dragon.version import __version__
from one_dragon_qt.services.styles_manager import OdQtStyleSheet
from one_dragon_qt.view.like_interface import LikeInterface
from one_dragon_qt.view.setting.setting_push_interface import SettingPushInterface
from one_dragon_qt.widgets.navigation_button import NavigationButton
from one_dragon_qt.windows.app_window_base import AppWindowBase
from script_chainer.context.script_chainer_context import ScriptChainerContext
from script_chainer.gui.page.desktop_clone_interface import DesktopCloneInterface
from script_chainer.gui.page.editor_setting_interface import EditorSettingInterface
from script_chainer.gui.page.script_setting_interface import ScriptSettingInterface


class AppWindow(AppWindowBase):
    HELP_URL = "https://one-dragon.com/tools/zh/script_chainer.html"

    def __init__(self, ctx: ScriptChainerContext, parent=None):
        """初始化主窗口类，设置窗口标题和图标"""
        self.ctx: ScriptChainerContext = ctx
        self._closing = False
        AppWindowBase.__init__(
            self,
            win_title="一条龙 千机链 配置器",
            project_config=ctx.project_config,
            app_icon="editor_icon.ico",
            parent=parent,
        )

    # 继承初始化函数
    def init_window(self):
        self.resize(960, 700)

        # 初始化位置
        self.move(100, 100)

        # 设置配置ID
        self.setObjectName("PhosWindow")
        self.navigationInterface.setObjectName("NavigationInterface")
        self.stackedWidget.setObjectName("StackedWidget")
        self.titleBar.setObjectName("TitleBar")

        # 布局样式调整
        self.hBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.areaLayout.setContentsMargins(0, 32, 0, 0)
        self.navigationInterface.setContentsMargins(0, 0, 0, 0)

        # 配置样式
        OdQtStyleSheet.NAVIGATION_INTERFACE.apply(self.navigationInterface)
        OdQtStyleSheet.STACKED_WIDGET.apply(self.stackedWidget)
        OdQtStyleSheet.AREA_WIDGET.apply(self.areaWidget)
        OdQtStyleSheet.TITLE_BAR.apply(self.titleBar)

        self.titleBar.setVersion(__version__)

    def create_sub_interface(self):
        """创建和添加各个子界面"""
        self.add_sub_interface(ScriptSettingInterface(self.ctx, parent=self))

        self.add_sub_interface(DesktopCloneInterface(self.ctx, parent=self))

        help_button = NavigationButton(
            object_name="script_chainer_help_button",
            text="说明",
            icon=FluentIcon.HELP,
            on_click=self.open_help_url,
            parent=self.navigationInterface,
        )
        help_button.setToolTip("使用说明")
        self.add_nav_widget(help_button, position=NavigationItemPosition.BOTTOM)

        self.add_sub_interface(
            LikeInterface(self.ctx, parent=self),
            position=NavigationItemPosition.BOTTOM,
        )

        self.add_sub_interface(
            SettingPushInterface(self.ctx, parent=self),
            position=NavigationItemPosition.BOTTOM,
        )

        self.add_sub_interface(
            EditorSettingInterface(self.ctx, parent=self),
            position=NavigationItemPosition.BOTTOM,
        )

    def closeEvent(self, event) -> None:  # noqa: N802
        """关窗口时问一句要不要把桌面分身一起注销。

        子会话是独立登录的用户桌面，程序退出了它还会留在后台（里面可能还开着
        游戏），所以这里必须由用户确认后再决定。
        """
        if self._closing:
            # 注销时为了让界面不假死会转几圈事件循环，期间再点一次关闭
            # 会重入这里、叠出第二个确认框，直接忽略
            event.ignore()
            return

        self._closing = True
        try:
            service = self.ctx.desktop_clone_service
            log.info(f"关闭配置器：分身会话 id={service.session_id}")
            if service.session_id > 0 and self._confirm_close_desktop_clone(service):
                self._logoff_desktop_clone(service)
            self._close_desktop_clone_pages()
            log.info("关闭配置器：正在结束桌面分身宿主")
            service.shutdown()
            log.info("关闭配置器：窗口即将关闭")
        except Exception as e:
            log.error(f"关闭配置器时出错：{e}", exc_info=True)
        finally:
            self._closing = False

        super().closeEvent(event)
        # 兜底：注销时转过事件循环，万一有什么把关闭吃掉了，这里直接退进程
        QTimer.singleShot(0, self._quit_if_still_visible)

    def _quit_if_still_visible(self) -> None:
        """关闭事件处理完窗口还在，就强制退出（否则会像「点了没反应」）。"""
        try:
            visible = self.isVisible()
        except RuntimeError:
            return
        if visible:
            log.warning("关闭后窗口仍然可见，强制退出程序")
            QApplication.quit()

    def _confirm_close_desktop_clone(self, service) -> bool:
        """问用户：注销分身并关闭，还是只关窗口。返回 True 表示要注销。"""
        box = _make_message_box(
            self,
            title="关闭千机链",
            text="桌面分身里还有正在运行的会话，关闭时是否一并注销？",
            informative="注销会关掉分身里正在运行的程序。",
        )
        logoff_btn = box.addButton("注销并关闭", QMessageBox.ButtonRole.AcceptRole)
        close_btn = box.addButton("仅关闭", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(close_btn)
        self._exec_dialog_without_picture(service, box)
        clicked = box.clickedButton()
        log.info(f"关闭确认框：{clicked.text() if clicked is not None else '取消'}")
        return clicked is logoff_btn

    def _exec_dialog_without_picture(self, service, dialog) -> int:
        """弹确认框前先把画面收起来，再把对话框提到最上层。

        画面是宿主进程里的原生窗口（嵌在页面里时是子窗口，小窗时是置顶的
        独立窗口），模态对话框会被它压在下面；以前的 qfluentwidgets
        MessageBox 还是一个覆盖整窗的半透明无边框窗口，按钮常常收不到点击。
        现在对话框本身用标准 QMessageBox，且显式置顶。
        """
        state = service.suspend_overlay()
        try:
            _raise_native_window(dialog)
            return int(dialog.exec())
        finally:
            service.resume_overlay(state)

    @staticmethod
    def _logoff_desktop_clone(service, timeout_ms: int = 5000) -> None:
        """发注销命令并等宿主把会话关掉，避免进程先退、注销没执行。"""
        log.info("关闭配置器：正在注销桌面分身子会话")
        service.logoff()
        deadline = time.time() + timeout_ms / 1000.0
        while service.session_id > 0 and time.time() < deadline:
            QApplication.processEvents()
            time.sleep(0.05)
        log.info(f"关闭配置器：注销结束，剩余会话 id={service.session_id}")

    def _close_desktop_clone_pages(self) -> None:
        """把页面里的小窗收掉，免得留下一个孤立的画面窗口。"""
        for index in range(self.stackedWidget.count()):
            page = self.stackedWidget.widget(index)
            close_mini = getattr(page, "_close_mini_window", None)
            if callable(close_mini):
                close_mini()

    def open_help_url(self) -> None:
        box = _make_message_box(
            self,
            title="打开使用说明",
            text="即将在浏览器中打开千机链使用说明，是否继续？",
            informative=None,
        )
        open_btn = box.addButton("打开", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(open_btn)
        service = self.ctx.desktop_clone_service
        self._exec_dialog_without_picture(service, box)
        if box.clickedButton() is open_btn:
            QDesktopServices.openUrl(QUrl(self.HELP_URL))


def _make_message_box(
    parent, title: str, text: str, informative: str | None
) -> QMessageBox:
    """统一外观的确认框：不透明、标准窗口，不会像半透明遮罩窗口那样丢点击。"""
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setIcon(QMessageBox.Icon.Question)
    box.setText(text)
    if informative:
        box.setInformativeText(informative)
    box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    box.setWindowModality(Qt.WindowModality.ApplicationModal)
    return box


def _raise_native_window(dialog) -> None:
    """把对话框抬到置顶层并抢到前台。

    画面是另一个进程的原生窗口，Qt 的 raise_() 管不到它，所以先 show 出
    真正的句柄，再用 Win32 的 SetWindowPos 抬一次、SetForegroundWindow 抢焦点。
    """
    dialog.show()
    QApplication.processEvents()
    dialog.raise_()
    dialog.activateWindow()
    try:
        hwnd = int(dialog.winId())
        win32gui.SetWindowPos(
            hwnd,
            win32con.HWND_TOPMOST,
            0,
            0,
            0,
            0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE,
        )
        win32gui.SetForegroundWindow(hwnd)
    except Exception as e:
        log.warning(f"把对话框提到最上层失败：{e}")


def run_editor():
    ctx = ScriptChainerContext()
    ctx.init()
    setTheme(Theme[ctx.custom_config.theme.upper()], lazy=True)
    app = QApplication([])
    app.setAttribute(Qt.ApplicationAttribute.AA_DontCreateNativeWidgetSiblings)
    window = AppWindow(ctx)
    window.show()
    window.activateWindow()
    app.exec()
    ctx.after_app_shutdown()


if __name__ == "__main__":
    run_editor()
