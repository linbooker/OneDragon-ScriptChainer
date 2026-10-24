import time

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication
from qfluentwidgets import (
    Dialog,
    FluentIcon,
    MessageBox,
    NavigationItemPosition,
    Theme,
    setTheme,
)

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
        service = self.ctx.desktop_clone_service
        if service.session_id > 0:
            box = MessageBox(
                "关闭千机链",
                "桌面分身里还有正在运行的会话，关闭时是否一并注销？",
                self,
            )
            box.yesButton.setText("注销并关闭")
            box.cancelButton.setText("仅关闭")
            if box.exec():
                self._logoff_desktop_clone(service)
        self._close_desktop_clone_pages()
        service.shutdown()
        super().closeEvent(event)

    @staticmethod
    def _logoff_desktop_clone(service, timeout_ms: int = 5000) -> None:
        """发注销命令并等宿主把会话关掉，避免进程先退、注销没执行。"""
        service.logoff()
        deadline = time.time() + timeout_ms / 1000.0
        while service.session_id > 0 and time.time() < deadline:
            QApplication.processEvents()
            time.sleep(0.05)

    def _close_desktop_clone_pages(self) -> None:
        """把页面里的小窗收掉，免得留下一个孤立的画面窗口。"""
        for index in range(self.stackedWidget.count()):
            page = self.stackedWidget.widget(index)
            close_mini = getattr(page, "_close_mini_window", None)
            if callable(close_mini):
                close_mini()

    def open_help_url(self) -> None:
        dialog = Dialog(
            "打开使用说明",
            "即将在浏览器中打开千机链使用说明，是否继续？",
            parent=self,
        )
        dialog.setTitleBarVisible(False)
        dialog.yesButton.setText("打开")
        dialog.cancelButton.setText("取消")
        if dialog.exec():
            QDesktopServices.openUrl(QUrl(self.HELP_URL))


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
