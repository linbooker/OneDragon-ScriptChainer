"""把程序（或脚本链）直接启动到桌面分身里。

对应启动参数 ``--desktop-clone``：

* ``--onedragon --chain 01 --desktop-clone``：在分身里跑脚本链 01；
* 只有 ``--desktop-clone``：在分身里打开千机链界面。

做法和界面上的「启动到分身」一样：起一个宿主进程连上子会话，再用任务计划
程序把本程序丢进那个会话里跑，最后把宿主关掉——子会话里的进程不受影响。
"""

import os
import sys
import time

from PySide6.QtCore import QCoreApplication

from one_dragon.utils import os_utils
from one_dragon.utils.log_utils import log
from script_chainer.config.desktop_clone_config import (
    DEFAULT_CHAIN_NAME,
    DesktopCloneConfig,
)
from script_chainer.services.desktop_clone.desktop_clone_service import (
    DesktopCloneService,
)

START_TIMEOUT_SECONDS = 20
"""等宿主进程起来。"""

CONNECT_TIMEOUT_SECONDS = 60
"""等子会话连上（第一次连接要建会话，慢一些）。"""

LAUNCH_TIMEOUT_SECONDS = 30
"""等程序在分身里启动。"""

POLL_INTERVAL_SECONDS = 0.05


class _ConfigHolder:
    """服务只用到 ``ctx.desktop_clone_config``，命令行里给个最小替身即可。"""

    def __init__(self, config: DesktopCloneConfig):
        self.desktop_clone_config = config


def _wait_until(predicate, timeout_seconds: float) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        QCoreApplication.processEvents()
        if predicate():
            return True
        time.sleep(POLL_INTERVAL_SECONDS)
    QCoreApplication.processEvents()
    return bool(predicate())


def _self_launch_command(
    chain_name: str | None,
    launch_gui: bool,
    extra_arguments: str = "",
) -> tuple[str, str, str]:
    """拼出「再启动一份本程序」的命令行。"""
    extra = (extra_arguments or "").strip()
    if launch_gui:
        target_args = extra
    else:
        chain = chain_name or DEFAULT_CHAIN_NAME
        target_args = f"--onedragon --chain {chain}"
        if extra:
            target_args = f"{target_args} {extra}"

    exe_path = sys.executable
    if os_utils.run_in_exe():
        return exe_path, target_args, os.path.dirname(exe_path)

    # 源码运行时用 -c 引导，保证分身里的进程能 import 到 one_dragon
    src_dir = os.path.join(os_utils.get_work_dir(), "src")
    bootstrap = (
        "import sys;"
        f"sys.path.insert(0, r'{src_dir}');"
        "from script_chainer.win_exe.launcher import main;"
        "main()"
    )
    return (
        exe_path,
        f'-c "{bootstrap}" {target_args}'.strip(),
        os_utils.get_work_dir(),
    )


def launch_in_desktop_clone(
    chain_name: str | None = None,
    launch_gui: bool = False,
    extra_arguments: str = "",
) -> int:
    """把本程序启动到桌面分身里。

    Returns:
        进程退出码：0 表示已在分身里启动成功。
    """
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication(sys.argv[:1])

    config = DesktopCloneConfig()
    service = DesktopCloneService(_ConfigHolder(config))

    def print_message(text: str) -> None:
        print(text, flush=True)

    service.message.connect(print_message)

    fatal_issues = [
        issue for issue in service.collect_environment_issues() if issue.fatal
    ]
    if fatal_issues:
        print(f"无法使用桌面分身：{fatal_issues[0].message}", flush=True)
        return 2

    print("正在启动桌面分身…", flush=True)
    if not service.start_host(0):
        print("启动桌面分身失败，请查看日志", flush=True)
        return 2

    # 命令行下不需要看画面，把宿主窗口藏起来
    service.set_embedded_visible(False)

    if not _wait_until(lambda: service.is_host_alive, START_TIMEOUT_SECONDS):
        print("桌面分身宿主没有起来", flush=True)
        service.shutdown()
        return 2

    print("正在连接分身桌面…", flush=True)
    service.connect_desktop()
    if not _wait_until(lambda: service.session_id > 0, CONNECT_TIMEOUT_SECONDS):
        print("连接桌面分身超时，请检查环境（管理员权限、登录密码）", flush=True)
        service.shutdown()
        return 3

    session_id = service.session_id
    exe_path, arguments, work_directory = _self_launch_command(
        chain_name=chain_name,
        launch_gui=launch_gui,
        extra_arguments=extra_arguments,
    )
    log.info(f"在桌面分身（会话 {session_id}）里启动：{exe_path} {arguments}")

    launched_pids: list[int] = []
    service.launched.connect(launched_pids.append)
    service.launch(exe_path, arguments, work_directory)

    if not _wait_until(lambda: bool(launched_pids), LAUNCH_TIMEOUT_SECONDS):
        print("在桌面分身里启动程序超时", flush=True)
        service.shutdown()
        return 4

    if launch_gui:
        print(f"已在桌面分身（会话 {session_id}）里打开千机链界面", flush=True)
    else:
        print(
            f"已在桌面分身（会话 {session_id}）里启动脚本链 "
            f"{chain_name or DEFAULT_CHAIN_NAME}",
            flush=True,
        )

    service.shutdown()
    app.processEvents()
    return 0
