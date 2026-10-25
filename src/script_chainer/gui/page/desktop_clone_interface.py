"""桌面分身页面。

画面由宿主进程渲染进 ``DesktopCloneHostWidget`` 的子窗口里。宿主窗口是原生子窗口，
永远画在所有 Qt 控件上面，所以日志面板不能“浮”在画面上，只能和画面并排占一列。
"""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CheckBox,
    ComboBox,
    FluentIcon,
    LineEdit,
    PrimaryPushButton,
    PushButton,
)

from one_dragon.utils.i18_utils import gt
from one_dragon_qt.widgets.base_interface import BaseInterface
from script_chainer.config.desktop_clone_config import DesktopResolution
from script_chainer.gui.widget.desktop_clone_host_widget import DesktopCloneHostWidget
from script_chainer.gui.widget.desktop_clone_mini_window import DesktopCloneMiniWindow
from script_chainer.services.desktop_clone.host_process import (
    STATE_CONNECTED,
    STATE_CONNECTING,
    STATE_RECONNECTING,
)

LOG_MAX_LINES = 500
LOG_PANEL_WIDTH = 380

# 连接超过这个时间还没结果，就认为卡住了，把界面放开让用户重试
CONNECT_TIMEOUT_MILLIS = 25000


class DesktopCloneInterface(BaseInterface):
    """桌面分身。"""

    def __init__(self, ctx, parent=None):
        self.ctx = ctx
        self.service = ctx.desktop_clone_service

        BaseInterface.__init__(
            self,
            object_name="desktop_clone_interface",
            nav_text_cn="桌面分身",
            nav_icon=FluentIcon.PEOPLE,
            parent=parent,
        )

        self._shown_once = False
        self._config_bound = False
        self._log_panel_visible = True
        self.mini_window: DesktopCloneMiniWindow | None = None
        self._page_overlay_state = None

        self._build_ui()

        self.service.state_changed.connect(self._on_state_changed)
        self.service.message.connect(self._on_message)
        self.service.chain_log.connect(self._on_chain_log)
        # 弹确认框时把画面临时收起来：画面是宿主进程的原生窗口，模态对话框
        # 压在它下面会点不到（小窗还是独立的置顶窗口，更要收）
        self.service.set_overlay_hooks(
            self._suspend_picture_for_dialog,
            self._resume_picture_after_dialog,
        )

    # ---------- 界面 ----------

    def _build_ui(self) -> None:
        self.host_widget = DesktopCloneHostWidget(self)
        self.host_widget.stage_resized.connect(self._on_stage_resized)

        self.status_label = QLabel("", self)
        self.status_label.setStyleSheet("color: #8a8a8a;")

        # 画面是宿主进程里的一块子窗口，页面上的提示不会被它挡住，
        # 但仍然不用全局 InfoBar：这里的操作多半是「过几秒就没了」的状态提示
        self.notice_label = QLabel("", self)
        self.notice_label.setWordWrap(True)
        self.notice_label.setVisible(False)
        self.notice_label.setStyleSheet(
            "QLabel { color: #1a1a1a; background: #fff4ce; border: 1px solid #e0c060;"
            " border-radius: 4px; padding: 6px 10px; }"
        )

        # 连接是异步的，万一宿主/RDP 控件一直不回话，超时后把按钮放开
        self._connect_watchdog = QTimer(self)
        self._connect_watchdog.setSingleShot(True)
        self._connect_watchdog.timeout.connect(self._on_connect_timeout)

        self._notice_timer = QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.timeout.connect(self._hide_notice)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(11, 11, 11, 11)
        layout.setSpacing(8)
        layout.addWidget(self._build_toolbar())
        layout.addWidget(self.notice_label)

        # 画面和日志并排：宿主窗口是原生子窗口，会盖住同一窗口里所有 Qt 控件，
        # 所以日志只能占一列，不能盖在画面上
        content = QWidget(self)
        content_row = _hbox(content, spacing=8)
        content_row.addWidget(self.host_widget, 1)

        self.log_panel = self._build_log_panel()
        self.log_panel.setFixedWidth(LOG_PANEL_WIDTH)
        self.log_panel.setVisible(self._log_panel_visible)
        content_row.addWidget(self.log_panel, 0)

        layout.addWidget(content, 1)
        layout.addWidget(self._build_settings_bar())
        layout.addWidget(self.status_label)

    def _build_toolbar(self) -> QWidget:
        bar = QWidget(self)
        row = _hbox(bar, spacing=8)

        self.start_btn = PrimaryPushButton(FluentIcon.PLAY, gt("启动画面"), bar)
        self.start_btn.clicked.connect(self._on_start_clicked)
        row.addWidget(self.start_btn)

        self.connect_btn = PushButton(FluentIcon.LINK, gt("连接"), bar)
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        row.addWidget(self.connect_btn)

        self.disconnect_btn = PushButton(FluentIcon.CANCEL, gt("断开"), bar)
        self.disconnect_btn.clicked.connect(self._on_disconnect_clicked)
        row.addWidget(self.disconnect_btn)

        self.launch_btn = PushButton(FluentIcon.GAME, gt("启动到分身"), bar)
        self.launch_btn.clicked.connect(self._on_launch_clicked)
        row.addWidget(self.launch_btn)

        self.wind_btn = PushButton(FluentIcon.SEND, gt("Win+D"), bar)
        self.wind_btn.clicked.connect(lambda: self.service.send_shortcut("wind"))
        row.addWidget(self.wind_btn)

        self.wintab_btn = PushButton(FluentIcon.SEND, gt("Win+Tab"), bar)
        self.wintab_btn.clicked.connect(lambda: self.service.send_shortcut("wintab"))
        row.addWidget(self.wintab_btn)

        self.mini_btn = PushButton(FluentIcon.VIEW, gt("小窗"), bar)
        self.mini_btn.setCheckable(True)
        self.mini_btn.clicked.connect(self._on_mini_clicked)
        row.addWidget(self.mini_btn)

        self.logoff_btn = PushButton(FluentIcon.POWER_BUTTON, gt("注销分身"), bar)
        self.logoff_btn.clicked.connect(self._on_logoff_clicked)
        row.addWidget(self.logoff_btn)

        row.addStretch(1)

        self.log_btn = PushButton(FluentIcon.DOCUMENT, gt("日志"), bar)
        self.log_btn.clicked.connect(self._toggle_log_panel)
        row.addWidget(self.log_btn)

        self.env_btn = PushButton(FluentIcon.INFO, gt("检查环境"), bar)
        self.env_btn.clicked.connect(self._on_check_env_clicked)
        row.addWidget(self.env_btn)

        return bar

    def _build_settings_bar(self) -> QWidget:
        """画面下方的设置区：挤在一行太窄，拆成两行。"""
        bar = QWidget(self)
        column = QVBoxLayout(bar)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)

        # 第一行：分辨率 + 几个开关
        first = _hbox(None, spacing=10)
        first.addWidget(QLabel(gt("分辨率"), bar))
        self.resolution_cb = ComboBox(bar)
        self.resolution_cb.setFixedWidth(150)
        for item in DesktopResolution:
            self.resolution_cb.addItem(item.value.ui_text, userData=item)
        first.addWidget(self.resolution_cb)

        first.addSpacing(6)
        self.watch_mode_cb = CheckBox(gt("观看模式"), bar)
        self.smart_sizing_cb = CheckBox(gt("自适应"), bar)
        self.audio_muted_cb = CheckBox(gt("静音"), bar)
        self.syskeys_cb = CheckBox(gt("系统快捷键送分身"), bar)
        for box in (
            self.watch_mode_cb,
            self.smart_sizing_cb,
            self.audio_muted_cb,
            self.syskeys_cb,
        ):
            first.addWidget(box)
        first.addStretch(1)
        column.addLayout(first)

        # 第二行：启动到分身用的程序和参数
        second = _hbox(None, spacing=10)
        second.addWidget(QLabel(gt("程序"), bar))
        self.target_program_edit = LineEdit(bar)
        self.target_program_edit.setMinimumWidth(260)
        self.target_program_edit.setClearButtonEnabled(True)
        self.target_program_edit.setPlaceholderText(
            self.ctx.desktop_clone_config.target_program
        )
        second.addWidget(self.target_program_edit)

        second.addWidget(QLabel(gt("参数"), bar))
        self.target_arguments_edit = LineEdit(bar)
        self.target_arguments_edit.setMinimumWidth(200)
        self.target_arguments_edit.setClearButtonEnabled(True)
        self.target_arguments_edit.setPlaceholderText("--onedragon --chain 01")
        second.addWidget(self.target_arguments_edit)

        second.addWidget(QLabel(gt("脚本链"), bar))
        self.chain_name_edit = LineEdit(bar)
        self.chain_name_edit.setFixedWidth(70)
        self.chain_name_edit.setClearButtonEnabled(True)
        self.chain_name_edit.setPlaceholderText("01")
        second.addWidget(self.chain_name_edit)

        second.addStretch(1)
        column.addLayout(second)
        return bar

    def _build_log_panel(self) -> QWidget:
        """和画面并排的日志面板。"""
        panel = QWidget(self)
        panel.setObjectName("desktop_clone_log_panel")
        panel.setStyleSheet(
            "#desktop_clone_log_panel { background: rgba(20, 20, 24, 225);"
            " border: 1px solid #3a3a40; border-radius: 6px; }"
        )

        column = QVBoxLayout(panel)
        column.setContentsMargins(8, 6, 8, 8)
        column.setSpacing(4)

        head = QWidget(panel)
        head_row = _hbox(head, spacing=6)
        title = QLabel(gt("日志"), head)
        title.setStyleSheet("color: #d0d0d0;")
        head_row.addWidget(title)
        head_row.addStretch(1)
        self.log_clear_btn = PushButton(FluentIcon.DELETE, gt("清空"), head)
        self.log_clear_btn.clicked.connect(self._on_clear_log)
        head_row.addWidget(self.log_clear_btn)
        self.log_close_btn = PushButton(FluentIcon.CLOSE, gt("关闭"), head)
        self.log_close_btn.clicked.connect(self._toggle_log_panel)
        head_row.addWidget(self.log_close_btn)
        column.addWidget(head)

        self.log_view = QPlainTextEdit(panel)
        self.log_view.setObjectName("desktop_clone_log_view")
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet(
            "#desktop_clone_log_view { background: #1b1b1f; color: #d8d8d8;"
            " border: none; font-family: Consolas, 'Microsoft YaHei UI';"
            " font-size: 12px; }"
        )
        self.log_view.setPlaceholderText(gt("桌面分身的日志会输出到这里"))
        column.addWidget(self.log_view, 1)

        return panel

    def _toggle_log_panel(self) -> None:
        self._log_panel_visible = not self._log_panel_visible
        self.log_panel.setVisible(self._log_panel_visible)

    def _on_clear_log(self) -> None:
        self.log_view.clear()

    # ---------- 生命周期 ----------

    def on_interface_shown(self) -> None:
        BaseInterface.on_interface_shown(self)
        self._bind_config()
        self._refresh_status()
        # 离开这一页时收起来的画面，回到这一页再放出去
        self._resume_page_overlay()
        self._sync_embed_target()

        if not self._shown_once:
            self._shown_once = True
            if self.ctx.desktop_clone_config.auto_connect:
                self._start_host()

    def on_interface_hidden(self) -> None:
        BaseInterface.on_interface_hidden(self)
        # 画面是宿主进程里的原生窗口，停在这一页以外它照样浮在上面：嵌在
        # 页面里时它是主窗口的原生子窗口，漂成小窗时更是独立的置顶窗口。
        # 别的页面弹出遮罩对话框会被它压住、点不到，所以离开这一页就把
        # 画面整个收起来（只暂停覆盖层，不改对话框自己的样式），回来再放回去
        self._page_overlay_state = self.service.suspend_overlay()

    def _resume_page_overlay(self) -> None:
        """回到这一页：把离开时暂停的画面恢复出来。"""
        state = self._page_overlay_state
        self._page_overlay_state = None
        if state is not None:
            self.service.resume_overlay(state)
        elif not self._mini_active():
            self.service.set_embedded_visible(True)

    # ---------- 嵌入 ----------

    def _sync_embed_target(self) -> None:
        """把画面控件的句柄和当前尺寸告诉服务。"""
        view = self._active_view()
        self.service.set_embed_parent(view.native_handle())
        self.service.set_embed_size(*view.stage_size())

    def _on_stage_resized(self) -> None:
        view = self._active_view()
        self.service.set_embed_size(*view.stage_size())

    # ---------- 小窗 ----------

    def _active_view(self) -> DesktopCloneHostWidget | DesktopCloneMiniWindow:
        """画面现在该贴在哪儿：页面里的舞台，还是独立小窗。"""
        if self._mini_active():
            return self.mini_window
        return self.host_widget

    def _mini_active(self) -> bool:
        return self.mini_window is not None and self.mini_window.isVisible()

    def _on_mini_clicked(self) -> None:
        if self._mini_active():
            self._exit_mini_mode()
        else:
            self._enter_mini_mode()

    def _enter_mini_mode(self) -> None:
        if not self.service.is_connected and not self.service.is_host_alive:
            self.mini_btn.setChecked(False)
            self._show_notice("桌面分身还没有连接，先连接再开小窗")
            return
        if not self.service.is_connected:
            self.mini_btn.setChecked(False)
            self._show_notice("桌面分身还没有连接好，连接成功后再开小窗")
            return

        window = self._ensure_mini_window()
        window.show()
        window.raise_()
        # show 之后才有真正的 HWND；句柄拿早了画面会仍留在主界面
        window.ensure_native_handle()
        self.mini_btn.setChecked(True)
        window.set_placeholder_text("")
        window.show_placeholder(False)
        self.service.set_embed_parent(window.native_handle())
        self.service.set_embed_size(*window.stage_size())
        self.service.set_embedded_visible(True)
        self.host_widget.show_placeholder(True)
        self.host_widget.set_placeholder_text("画面已经切到小窗")
        self._append_log("画面已切到小窗")

    def _exit_mini_mode(self) -> None:
        self.mini_btn.setChecked(False)
        if self.mini_window is None:
            return
        was_visible = self.mini_window.isVisible()
        self.mini_window.hide()
        if was_visible:
            self._append_log("画面已还原到页面")
        self._sync_embed_target()
        self.host_widget.show_placeholder(False)
        self.service.set_embedded_visible(self.isVisible())

    def _ensure_mini_window(self) -> DesktopCloneMiniWindow:
        if self.mini_window is not None:
            return self.mini_window

        window = DesktopCloneMiniWindow()
        window.set_pinned(self.ctx.desktop_clone_config.mini_pinned)
        window.restore_requested.connect(self._on_mini_restore)
        window.pin_changed.connect(self._on_mini_pin_changed)
        window.stage_resized.connect(self._on_mini_stage_resized)

        host = self.window()
        if host is not None:
            pos = host.geometry().topLeft()
            window.move(pos.x() + 60, pos.y() + 80)

        self.mini_window = window
        return window

    def _on_mini_restore(self) -> None:
        self._exit_mini_mode()
        self.mini_btn.setChecked(False)

    def _on_mini_pin_changed(self, pinned: bool) -> None:
        self.ctx.desktop_clone_config.update("mini_pinned", pinned)
        # 切换置顶要重建窗口标志，舞台的句柄会变，得把画面重新挂一次
        if self._mini_active():
            self._sync_embed_target()
            self.service.set_embedded_visible(True)

    def _on_mini_stage_resized(self) -> None:
        if self._mini_active():
            self.service.set_embed_size(*self.mini_window.stage_size())

    # ---------- 对话框期间收起画面 ----------

    def _suspend_picture_for_dialog(self) -> dict | None:
        """模态对话框要弹了：把画面（小窗）先收起来，返回恢复用的状态。"""
        if not self._mini_active():
            return None

        self.mini_window.hide()
        return {"mini": True}

    def _resume_picture_after_dialog(self, state) -> None:
        """对话框关掉后把小窗放回原位，并重新把画面挂上去。"""
        if not state or not state.get("mini") or self.mini_window is None:
            return

        self.mini_window.show()
        self.mini_window.raise_()
        self.mini_window.ensure_native_handle()
        self.service.set_embed_parent(self.mini_window.native_handle())
        self.service.set_embed_size(*self.mini_window.stage_size())
        # 显示与否交给 service.resume_overlay 按暂停前的状态决定，这里只负责挂回去

    def _close_mini_window(self) -> None:
        self.mini_btn.setChecked(False)
        if self.mini_window is None:
            return
        self.mini_window.hide()
        self.mini_window.deleteLater()
        self.mini_window = None

    def _bind_config(self) -> None:
        if self._config_bound:
            return
        self._config_bound = True

        config = self.ctx.desktop_clone_config

        index = 0
        for i, item in enumerate(DesktopResolution):
            if item.value.value == config.resolution:
                index = i
                break
        self.resolution_cb.setCurrentIndex(index)
        self.resolution_cb.currentIndexChanged.connect(self._on_resolution_changed)

        self.watch_mode_cb.setChecked(config.watch_mode)
        self.smart_sizing_cb.setChecked(config.smart_sizing)
        self.audio_muted_cb.setChecked(config.audio_muted)
        self.syskeys_cb.setChecked(config.send_system_shortcuts)

        self.watch_mode_cb.stateChanged.connect(self._on_setting_changed)
        self.smart_sizing_cb.stateChanged.connect(self._on_setting_changed)
        self.audio_muted_cb.stateChanged.connect(self._on_setting_changed)
        self.syskeys_cb.stateChanged.connect(self._on_setting_changed)

        self.target_program_edit.setText(config.target_program)
        self.target_arguments_edit.setText(config.target_arguments)
        self.chain_name_edit.setText(config.chain_name)

        self.target_program_edit.editingFinished.connect(self._on_target_changed)
        self.target_arguments_edit.editingFinished.connect(self._on_target_changed)
        self.chain_name_edit.editingFinished.connect(self._on_target_changed)

    # ---------- 状态 ----------

    def _refresh_status(self) -> None:
        self.status_label.setText(
            f"{gt('状态')}：{self.service.state_text} · {self.service.describe_environment()}"
        )
        alive = self.service.is_host_alive
        state = self.service.state
        connecting = state in (STATE_CONNECTING, STATE_RECONNECTING)
        connected = state == STATE_CONNECTED

        self.start_btn.setEnabled(not alive)
        self.connect_btn.setEnabled(alive and not connecting and not connected)
        self.disconnect_btn.setEnabled(alive and (connecting or connected))
        self.launch_btn.setEnabled(alive and self.service.session_id > 0)
        self.wind_btn.setEnabled(alive and connected)
        self.wintab_btn.setEnabled(alive and connected)
        self.logoff_btn.setEnabled(alive and self.service.session_id > 0)

        view = self._active_view()
        if not alive:
            view.set_placeholder_text("桌面分身尚未启动，点「启动画面」")
        elif connected:
            view.set_placeholder_text("")
        elif connecting:
            view.set_placeholder_text("正在连接桌面分身...")
        elif self.service.last_error:
            view.set_placeholder_text(self.service.last_error)
        else:
            view.set_placeholder_text("桌面分身未连接，点「连接」")

        view.show_placeholder(not connected)

    def _on_state_changed(self) -> None:
        if self.service.state not in (STATE_CONNECTING, STATE_RECONNECTING):
            self._connect_watchdog.stop()
        self._refresh_status()

    def _on_message(self, text: str) -> None:
        if not text:
            return
        self._append_log(text)
        self._show_notice(text)

    def _on_chain_log(self, text: str) -> None:
        """分身里那个千机链的日志：加个前缀，和本界面的消息区分开。"""
        if text:
            self._append_log(f"分身 | {text}")

    def _append_log(self, text: str) -> None:
        # 只往界面日志框里写，不写回日志文件：转进来的分身日志就是从日志文件
        # 读出来的，写回去会被再读一遍，转一圈就成了自我复制的刷屏
        view = getattr(self, "log_view", None)
        if view is None:
            return

        view.appendPlainText(text)
        # 日志框只留最近若干行，不然跑一晚上会堆到内存里
        if view.blockCount() > LOG_MAX_LINES:
            cursor = view.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            cursor.movePosition(
                cursor.MoveOperation.Down,
                cursor.MoveMode.KeepAnchor,
                view.blockCount() - LOG_MAX_LINES,
            )
            cursor.removeSelectedText()
        view.verticalScrollBar().setValue(view.verticalScrollBar().maximum())

    def _show_notice(self, text: str, duration_ms: int = 6000) -> None:
        """在页面里显示一条提示。"""
        if not text:
            return
        self.notice_label.setText(text)
        self.notice_label.setVisible(True)
        self._notice_timer.start(duration_ms)

    def _hide_notice(self) -> None:
        self.notice_label.setVisible(False)

    # ---------- 操作 ----------

    def _start_host(self) -> bool:
        self._sync_embed_target()
        window = self.window()
        owner = int(window.winId()) if window is not None else 0
        ok = self.service.start_host(owner)
        if ok:
            self.host_widget.set_placeholder_text("正在连接桌面分身...")
            self.host_widget.show_placeholder(True)
        self._refresh_status()
        return ok

    def _on_start_clicked(self) -> None:
        self._start_host()

    def _on_connect_clicked(self) -> None:
        if not self.service.is_host_alive and not self._start_host():
            return
        self.service.connect_desktop()
        self._connect_watchdog.start(CONNECT_TIMEOUT_MILLIS)
        self._refresh_status()

    def _on_connect_timeout(self) -> None:
        if self.service.state not in (STATE_CONNECTING, STATE_RECONNECTING):
            return
        self._append_log("连接超时，已取消本次连接")
        self._show_notice("连接超时，已取消，请重试")
        self.service.cancel_connect()
        self._refresh_status()

    def _on_disconnect_clicked(self) -> None:
        self.service.disconnect_desktop()
        self._refresh_status()

    def _on_launch_clicked(self) -> None:
        self.service.launch_configured_program()

    def _on_logoff_clicked(self) -> None:
        self._exit_mini_mode()
        self.service.logoff()
        self._refresh_status()

    def _on_setting_changed(self) -> None:
        config = self.ctx.desktop_clone_config
        config.update("watch_mode", self.watch_mode_cb.isChecked())
        config.update("smart_sizing", self.smart_sizing_cb.isChecked())
        config.update("audio_muted", self.audio_muted_cb.isChecked())
        config.update("send_system_shortcuts", self.syskeys_cb.isChecked())
        self.service.apply_settings()

    def _on_target_changed(self) -> None:
        config = self.ctx.desktop_clone_config
        config.update("target_program", self.target_program_edit.text().strip())
        config.update("target_arguments", self.target_arguments_edit.text().strip())
        config.update("chain_name", self.chain_name_edit.text().strip() or "01")

    def _on_resolution_changed(self, index: int) -> None:
        item = self.resolution_cb.itemData(index)
        if item is None:
            return
        self.ctx.desktop_clone_config.update("resolution", item.value.value)
        width, height = self.service.config.resolution_size
        self.service.change_resolution(width, height)
        self._append_log(f"切换分身分辨率：{width}x{height}（需要重新连接才会生效）")

    def _on_check_env_clicked(self) -> None:
        issues = self.service.collect_environment_issues()
        if not issues:
            self._show_notice(gt("环境看起来没问题，可以启动桌面分身"))
            return

        text = "；".join(f"{issue.title}：{issue.message}" for issue in issues)
        self._show_notice(text)
        self._refresh_status()


def _hbox(parent: QWidget | None, spacing: int = 8) -> QHBoxLayout:
    """建一个不带边距的横向布局；parent 为 None 时由调用方自己 addLayout()。"""
    layout = QHBoxLayout(parent) if parent is not None else QHBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    return layout
