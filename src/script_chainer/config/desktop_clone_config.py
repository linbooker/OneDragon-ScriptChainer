import sys
from enum import Enum

from one_dragon.base.config.config_item import ConfigItem
from one_dragon.base.config.yaml_config import YamlConfig

DEFAULT_TARGET_ARGUMENTS = "--onedragon --chain 01"
DEFAULT_CHAIN_NAME = "01"


class DesktopResolution(Enum):
    """桌面分身的虚拟桌面分辨率。

    与 BetterGenshinImpact 一致，默认 1920x1080；
    更大的分辨率在嵌入的窗口里会被等比缩放显示。
    """

    R1600X900 = ConfigItem(label="1600 x 900", value="1600x900")
    R1920X1080 = ConfigItem(label="1920 x 1080", value="1920x1080")
    R2560X1440 = ConfigItem(label="2560 x 1440", value="2560x1440")


class DesktopCloneConfig(YamlConfig):
    """桌面分身配置。

    存放于 config/desktop_clone.yml。
    """

    def __init__(self):
        YamlConfig.__init__(self, module_name="desktop_clone")

    @property
    def resolution(self) -> str:
        return self.get("resolution", DesktopResolution.R1920X1080.value.value)

    @resolution.setter
    def resolution(self, value: str) -> None:
        self.update("resolution", value)

    @property
    def resolution_size(self) -> tuple[int, int]:
        """把配置中的 '1920x1080' 解析成 (宽, 高)。"""
        raw = self.resolution or ""
        try:
            width_text, height_text = raw.lower().split("x", 1)
            width, height = int(width_text), int(height_text)
        except (ValueError, AttributeError):
            return 1920, 1080

        if width < 200 or height < 200:
            return 1920, 1080
        return width, height

    @property
    def auto_connect(self) -> bool:
        """进入桌面分身页面时自动连接。"""
        return self.get("auto_connect", True)

    @auto_connect.setter
    def auto_connect(self, value: bool) -> None:
        self.update("auto_connect", value)

    @property
    def topmost(self) -> bool:
        """桌面分身窗口置顶。"""
        return self.get("topmost", False)

    @topmost.setter
    def topmost(self, value: bool) -> None:
        self.update("topmost", value)

    @property
    def mini_pinned(self) -> bool:
        """小窗是否置顶。"""
        return self.get("mini_pinned", True)

    @mini_pinned.setter
    def mini_pinned(self, value: bool) -> None:
        self.update("mini_pinned", value)

    @property
    def watch_mode(self) -> bool:
        """观看模式：屏蔽远程桌面的输入，避免画面里的鼠标乱动。"""
        return self.get("watch_mode", False)

    @watch_mode.setter
    def watch_mode(self, value: bool) -> None:
        self.update("watch_mode", value)

    @property
    def smart_sizing(self) -> bool:
        """画面自适应缩放。"""
        return self.get("smart_sizing", True)

    @smart_sizing.setter
    def smart_sizing(self, value: bool) -> None:
        self.update("smart_sizing", value)

    @property
    def audio_muted(self) -> bool:
        """禁用声音重定向（分身里的声音不会混到当前桌面）。"""
        return self.get("audio_muted", True)

    @audio_muted.setter
    def audio_muted(self, value: bool) -> None:
        self.update("audio_muted", value)

    @property
    def send_system_shortcuts(self) -> bool:
        """把 Win 等系统快捷键送到分身里，而不是被当前桌面吃掉。"""
        return self.get("send_system_shortcuts", True)

    @send_system_shortcuts.setter
    def send_system_shortcuts(self, value: bool) -> None:
        self.update("send_system_shortcuts", value)

    @property
    def target_program(self) -> str:
        """「启动到桌面分身」的目标程序路径。默认就是千机链自己。"""
        value = self.get("target_program", "")
        if value:
            return value
        return self.default_target_program

    @target_program.setter
    def target_program(self, value: str) -> None:
        self.update("target_program", value)

    @property
    def default_target_program(self) -> str:
        """默认目标程序：当前运行的本体（exe 或 python）。"""
        return sys.executable

    @property
    def target_arguments(self) -> str:
        """启动目标程序时附带的参数。"""
        return self.get("target_arguments", DEFAULT_TARGET_ARGUMENTS)

    @target_arguments.setter
    def target_arguments(self, value: str) -> None:
        self.update("target_arguments", value)

    @property
    def chain_name(self) -> str:
        """千机链脚本链名称。"""
        return self.get("chain_name", DEFAULT_CHAIN_NAME)

    @chain_name.setter
    def chain_name(self, value: str) -> None:
        self.update("chain_name", value)
