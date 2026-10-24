"""桌面分身页面。

把「在本机开一块第二桌面，把游戏/脚本放进去跑」这件事做成一页：
上面是画面，下面是工具栏、设置和日志。
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel, QPlainTextEdit, QVBoxLayout, QWidget
from qfluentwidgets import (
    FluentIcon,
    PrimaryPushButton,
    PushButton,
    SettingCardGroup,
    SingleDirectionScrollArea,
)

from one_dragon.utils.i18_utils import gt
from one_dragon_qt.utils.layout_utils import Margins
from one_dragon_qt.widgets.base_interface import BaseInterface
from one_dragon_qt.widgets.column import Column
from one_dragon_qt.widgets.row import Row
from one_dragon_qt.widgets.setting_card.combo_box_setting_card import (
    ComboBoxSettingCard,
)
from one_dragon_qt.widgets.setting_card.switch_setting_card import SwitchSettingCard
from one_dragon_qt.widgets.setting_card.text_setting_card import TextSettingCard
from script_chainer.config.desktop_clone_config import DesktopResolution
from script_chainer.context.script_chainer_context import ScriptChainerContext
from script_chainer.gui.widget.desktop_clone_host_widget import DesktopCloneHostWidget
from script_chainer.services.desktop_clone.host_process import (
    STATE_CONNECTED,
    STATE_CONNECTING,
    STATE_RECONNECTING,
)

SETTINGS_AREA_HEIGHT = 220
LOG_AREA_HEIGHT = 160
LOG_MAX_LINES = 500


class DesktopCloneInterface(BaseInterface):
    """桌面分身。"""

    def __init__(self, ctx: ScriptChainerContext, parent=None):
        self.ctx: ScriptChainerContext = ctx
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
        self._build_ui()

        self.service.state_changed.connect(self._on_state_changed)
        self.service.message.connect(self._on_message)

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
        self._notice_timer = QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.timeout.connect(self._hide_notice)

        main = Column(margins=Margins(11, 11, 11, 11), spacing=8)
        main.add_widget(self._build_toolbar())
        main.add_widget(self.notice_label)
        # 画面需要占满中间剩下的空间，这里直接加进垂直布局（带 alignment 会退化成按 sizeHint 摆放）
        main.v_layout.addWidget(self.host_widget, 1)
        main.add_widget(self.status_label)
        main.add_widget(self._build_settings_area())
        main.add_widget(self._build_log_area())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(main)

    def _build_toolbar(self) -> QWidget:
        row = Row(spacing=8)

        self.start_btn = PrimaryPushButton(FluentIcon.PLAY, gt("启动画面"), self)
        self.start_btn.clicked.connect(self._on_start_clicked)
        row.add_widget(self.start_btn)

        self.connect_btn = PushButton(FluentIcon.LINK, gt("连接"), self)
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        row.add_widget(self.connect_btn)

        self.disconnect_btn = PushButton(FluentIcon.CANCEL, gt("断开"), self)
        self.disconnect_btn.clicked.connect(self._on_disconnect_clicked)
        row.add_widget(self.disconnect_btn)

        self.launch_btn = PushButton(FluentIcon.GAME, gt("启动到分身"), self)
        self.launch_btn.clicked.connect(self._on_launch_clicked)
        row.add_widget(self.launch_btn)

        self.wind_btn = PushButton(FluentIcon.SEND, gt("Win+D"), self)
        self.wind_btn.clicked.connect(lambda: self.service.send_shortcut("wind"))
        row.add_widget(self.wind_btn)

        self.wintab_btn = PushButton(FluentIcon.SEND, gt("Win+Tab"), self)
        self.wintab_btn.clicked.connect(lambda: self.service.send_shortcut("wintab"))
        row.add_widget(self.wintab_btn)

        self.logoff_btn = PushButton(FluentIcon.POWER_BUTTON, gt("注销分身"), self)
        self.logoff_btn.clicked.connect(self._on_logoff_clicked)
        row.add_widget(self.logoff_btn)

        row.add_stretch(1)

        self.env_btn = PushButton(FluentIcon.INFO, gt("检查环境"), self)
        self.env_btn.clicked.connect(self._on_check_env_clicked)
        row.add_widget(self.env_btn)

        return row

    def _build_settings_area(self) -> QWidget:
        group = SettingCardGroup(gt("桌面分身设置"))

        self.resolution_opt = ComboBoxSettingCard(
            icon=FluentIcon.FIT_PAGE,
            title=gt("分身分辨率"),
            content=gt("分身里桌面的分辨率，切换后会自动重连"),
            options_enum=DesktopResolution,
        )
        group.addSettingCard(self.resolution_opt)

        self.auto_connect_opt = SwitchSettingCard(
            icon=FluentIcon.SYNC,
            title=gt("进入页面自动连接"),
            content=gt("打开这一页时自动建立到桌面分身的连接"),
        )
        group.addSettingCard(self.auto_connect_opt)

        self.watch_mode_opt = SwitchSettingCard(
            icon=FluentIcon.VIEW,
            title=gt("观看模式"),
            content=gt("屏蔽在画面上点击/拖动的输入，只用于观看"),
        )
        group.addSettingCard(self.watch_mode_opt)

        self.smart_sizing_opt = SwitchSettingCard(
            icon=FluentIcon.FIT_PAGE,
            title=gt("画面自适应"),
            content=gt("窗口大小变化时由远程桌面自动缩放画面"),
        )
        group.addSettingCard(self.smart_sizing_opt)

        self.audio_muted_opt = SwitchSettingCard(
            icon=FluentIcon.MUTE,
            title=gt("静音"),
            content=gt("不把分身里的声音重定向到当前桌面"),
        )
        group.addSettingCard(self.audio_muted_opt)

        self.syskeys_opt = SwitchSettingCard(
            icon=FluentIcon.SEND,
            title=gt("系统快捷键送到分身"),
            content=gt("Win 等系统快捷键只在分身里生效，不影响当前桌面"),
        )
        group.addSettingCard(self.syskeys_opt)

        self.target_program_input = TextSettingCard(
            icon=FluentIcon.APPLICATION,
            title=gt("启动的程序"),
            content=gt("默认是本程序（无界面运行脚本链）"),
            input_placeholder=self.ctx.desktop_clone_config.target_program,
            input_max_width=360,
        )
        group.addSettingCard(self.target_program_input)

        self.target_arguments_input = TextSettingCard(
            icon=FluentIcon.CODE,
            title=gt("启动参数"),
            input_placeholder="--onedragon --chain 01",
            input_max_width=360,
        )
        group.addSettingCard(self.target_arguments_input)

        self.chain_name_input = TextSettingCard(
            icon=FluentIcon.HISTORY,
            title=gt("脚本链名称"),
            content=gt("点击「启动到分身」时使用的脚本链"),
            input_placeholder="01",
            input_max_width=120,
        )
        group.addSettingCard(self.chain_name_input)

        scroll = SingleDirectionScrollArea(orient=Qt.Orientation.Vertical)
        scroll.setWidget(group)
        scroll.setFixedHeight(SETTINGS_AREA_HEIGHT)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        scroll.viewport().setStyleSheet("background: transparent;")

        return scroll

    def _build_log_area(self) -> QWidget:
        self.log_view = QPlainTextEdit(self)
        self.log_view.setObjectName("desktop_clone_log_view")
        self.log_view.setReadOnly(True)
        self.log_view.setFixedHeight(LOG_AREA_HEIGHT)
        self.log_view.setStyleSheet(
            "#desktop_clone_log_view { background: #1b1b1f; color: #d8d8d8;"
            " border: 1px solid #333337; border-radius: 4px; padding: 4px;"
            " font-family: Consolas, 'Microsoft YaHei UI'; font-size: 12px; }"
        )
        self.log_view.setPlaceholderText(gt("桌面分身的日志会输出到这里"))

        self.log_clear_btn = PushButton(FluentIcon.DELETE, gt("清空日志"), self)
        self.log_clear_btn.clicked.connect(self.log_view.clear)

        row = Row(spacing=8)
        row.add_widget(QLabel(gt("日志"), self))
        row.add_stretch(1)
        row.add_widget(self.log_clear_btn)

        column = Column(spacing=4)
        column.add_widget(row)
        column.add_widget(self.log_view)
        return column

    # ---------- 生命周期 ----------

    def on_interface_shown(self) -> None:
        BaseInterface.on_interface_shown(self)
        self._bind_config()
        self._refresh_status()
        # 切回这一页时控件可能刚拿到新的句柄，重新告诉宿主一次
        self._sync_embed_target()

        if not self._shown_once:
            self._shown_once = True
            if self.ctx.desktop_clone_config.auto_connect:
                self._start_host()

    # ---------- 嵌入 ----------

    def _sync_embed_target(self) -> None:
        """把画面控件的句柄和当前尺寸告诉服务。"""
        handle = self.host_widget.native_handle()
        self.service.set_embed_parent(handle)
        self.service.set_embed_size(*self.host_widget.stage_size())

    def _on_stage_resized(self) -> None:
        self.service.set_embed_size(*self.host_widget.stage_size())

    def _bind_config(self) -> None:
        if self._config_bound:
            return
        self._config_bound = True

        config = self.ctx.desktop_clone_config
        self.resolution_opt.init_with_adapter(config.get_prop_adapter("resolution"))
        self.auto_connect_opt.init_with_adapter(config.get_prop_adapter("auto_connect"))
        self.watch_mode_opt.init_with_adapter(config.get_prop_adapter("watch_mode"))
        self.smart_sizing_opt.init_with_adapter(config.get_prop_adapter("smart_sizing"))
        self.audio_muted_opt.init_with_adapter(config.get_prop_adapter("audio_muted"))
        self.syskeys_opt.init_with_adapter(
            config.get_prop_adapter("send_system_shortcuts")
        )
        self.target_program_input.init_with_adapter(
            config.get_prop_adapter("target_program")
        )
        self.target_arguments_input.init_with_adapter(
            config.get_prop_adapter("target_arguments")
        )
        self.chain_name_input.init_with_adapter(config.get_prop_adapter("chain_name"))

        self.resolution_opt.value_changed.connect(lambda *_: self._on_resolution_changed())
        self.watch_mode_opt.value_changed.connect(lambda *_: self._on_setting_changed())
        self.smart_sizing_opt.value_changed.connect(
            lambda *_: self._on_setting_changed()
        )
        self.audio_muted_opt.value_changed.connect(
            lambda *_: self._on_setting_changed()
        )
        self.syskeys_opt.value_changed.connect(lambda *_: self._on_setting_changed())

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

        if not alive:
            self.host_widget.set_placeholder_text("桌面分身尚未启动，点「启动画面」")
        elif connected:
            self.host_widget.set_placeholder_text("")
        elif connecting:
            self.host_widget.set_placeholder_text("正在连接桌面分身...")
        elif self.service.last_error:
            self.host_widget.set_placeholder_text(self.service.last_error)
        else:
            self.host_widget.set_placeholder_text("桌面分身未连接，点「连接」")

        self.host_widget.show_placeholder(not connected)

    def _on_state_changed(self) -> None:
        self._refresh_status()

    def _on_message(self, text: str) -> None:
        if not text:
            return
        self._append_log(text)
        self._show_notice(text)

    def _append_log(self, text: str) -> None:
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
        self._refresh_status()

    def _on_disconnect_clicked(self) -> None:
        self.service.disconnect_desktop()
        self._refresh_status()

    def _on_launch_clicked(self) -> None:
        self.service.launch_configured_program()

    def _on_logoff_clicked(self) -> None:
        self.service.logoff()
        self._refresh_status()

    def _on_setting_changed(self) -> None:
        self.service.apply_settings()

    def _on_resolution_changed(self) -> None:
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
