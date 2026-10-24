# 桌面分身

把游戏或一条龙启动到 Windows 的**子会话**（Child Session）里，让它们拥有一个独立的桌面，
你可以随时切回自己的桌面继续做别的事情。功能和 [BetterGI](https://github.com/babalae/better-genshin-impact) 的「桌面分身」一致。

## 原理

Windows 的远程桌面服务支持一种特殊的本地会话：`WTSEnableChildSessions` 打开后，
客户端可以通过 RDP 的 `ConnectToChildSession` 连到本机的子会话。
和普通的「远程桌面连自己」不同，子会话是一个**隔离的第二个桌面**：

* 主桌面上的鼠标、键盘、窗口不会被子会话影响，反之亦然；
* 子会话里的程序照常跑在真实显卡上（不是虚拟机的软件渲染）；
* 子会话有独立的 `SessionId`，程序可以用真实的窗口句柄、真实的输入。

Qt（PySide6）没有 ActiveX 容器，无法直接宿主 RDP 控件，所以这里用一个小而全的
C# 宿主进程实现画面：

```
OneDragon ScriptChainer (Python + Qt)          DesktopCloneHost.exe (C# / WinForms)
├── 页面工具条 / 设置卡片        stdin  →      connect / disconnect / logoff
├── QProcess 启动宿主            ───────       launch / shortcut / input ...
└── 每 250ms 把「画面」区域      stdout ←      ready / state / session / launched
    的屏幕位置发给宿主                            │
        ▲  无边框顶层窗口叠在画面上               │
        └─────────────────────────────────────┘
                                         MsRdpClient10 ActiveX（RDP 控件）
```

* 页面上的「画面」区域按 16:9 居中，宿主是一个**无边框顶层窗口**，由 Python 侧每 250ms
  把画面区域在屏幕上的位置/尺寸发给它，叠在画面区域正上方（不跨进程 `SetParent`，
  那种做法在按监视器 DPI 缩放的机器上会让窗口变成不可见的透明层）；
* 宿主声明按监视器 DPI 感知，Python 侧按屏幕缩放比例换算成真实像素再下发，缩放 125% / 150%
  的机器上位置和大小都对得上；
* 没连上时宿主把远程桌面控件整个藏起来（它内部的子窗口会盖住一切），直接在窗口上画状态提示文字；
* 分身画面是置顶窗口，会盖住主界面，所以页面里的提示不用全局 InfoBar，而是显示在
  工具栏下方的页面内提示条上（`notice_label`），过期自动隐藏；
* 窗口的上下层关系：本程序在前台时宿主窗口**置顶**，压在页面「画面」区域正上方；
  切到别的程序时自动取消置顶，不会一直挂在别人窗口上面。在画面里操作时前台窗口是宿主自己，同样保持置顶；
* 连接用的是 `localhost` + `ConnectToChildSession = true`，**不需要账号密码**（子会话是当前登录用户的会话）；
* 宿主会读写一行一条的文本协议，Python 侧只需要读 stdout，不用处理复杂的 COM 事件。

## 环境要求

| 项目 | 要求 |
| --- | --- |
| 系统 | Windows 10 / 11（需要远程桌面服务在位） |
| 权限 | **必须管理员**。程序启动时会自动提权，与 BetterGI 相同 |
| 远程桌面 | 不能处于「禁用远程桌面」状态；装了 RDP Wrapper 的机器不兼容 |
| 登录方式 | 只允许 Windows Hello（无密码）的账户无法连接子会话，请改用密码登录 |

页面上的「检查环境」按钮会逐条列出当前机器的问题，红色问题必须先解决。

## 使用

1. 打开「桌面分身」页面 → 点「检查环境」，确认没有阻塞问题；
2. 点「启动画面」，程序会开启子会话能力（若尚未开启）并连接，第一次连接可能要十几秒；
3. 连上后点「启动到分身」：
   * 目标程序留空时，启动的是当前的一条龙（带 `--onedragon --chain <脚本链>`），
     适合「把这条脚本链扔到分身里跑」；
   * 也可以在设置里把目标程序改成游戏 exe，指定工作目录与启动参数。
4. 「注销分身」会断开会话并注销子会话（里面的程序会被关掉）；
   只点「断开」时画面会关掉，但**子会话和里面的程序继续运行**，
   下次点「启动画面」会重新连回同一个会话；切到别的页面只是把画面隐藏起来，连接不会断。

## 设置项

| 设置 | 说明 |
| --- | --- |
| 分辨率 | 子会话的桌面大小，窗口会自动等比缩放 |
| 打开页面时自动连接 | 进入页面时自动启动画面 |
| 观看模式 | 屏蔽向子会话发送的输入，只用来观察 |
| 智能缩放 | 画面跟随控件大小缩放（关闭后按分辨率显示） |
| 静音 | 不把子会话的声音转发出来 |
| 发送系统快捷键 | 允许 Win 键等系统组合键进入子会话；关闭时这些键留在主桌面 |
| 保持在其他窗口之上 | 宿主窗口不能被别的窗口挡住（仅在本程序内生效） |
| 目标程序 / 启动参数 / 脚本链 | 「启动到分身」使用的命令 |

## 文件

```
assets/desktop_clone/host/         C# 宿主源码（首次使用时用系统自带 csc.exe 编译）
assets/desktop_clone/bin/          可选的预编译 exe，发布包构建时由 CI 生成
src/script_chainer/config/desktop_clone_config.py
src/script_chainer/services/desktop_clone/
    native_api.py                  WTS / 注册表检查（子会话、端口、策略）
    host_builder.py                定位或编译宿主 exe（按源码哈希缓存）
    host_process.py                QProcess + 行协议
    desktop_clone_service.py       状态机与对外接口
src/script_chainer/gui/page/desktop_clone_interface.py   页面
src/script_chainer/gui/widget/desktop_clone_host_widget.py   显示画面的控件
```

宿主 exe 优先从 `assets/desktop_clone/bin/DesktopCloneHost.exe` 读取（发布包里有），
没有源码级别改动时也可以用环境变量 `ONE_DRAGON_DESKTOP_CLONE_HOST` 指定任意一个。
源码模式下第一次使用会现场编译，结果缓存在
`%LOCALAPPDATA%\OneDragonScriptChainer\desktop_clone\DesktopCloneHost-<哈希>.exe`。

## 常见问题

* **连接失败 / 画面一直黑**：先确认是管理员运行、远程桌面没有被组策略关闭；
  装了 RDP Wrapper 的机器也不支持。开启了「仅允许 Windows Hello 无密码登录」的账户
  可能连不上，需要在「设置 - 账户 - 登录选项」里关掉该选项（这一项只提示，不阻止启动）。
* **点了「连接」状态一直停在未连接**：宿主进程没读到手写的命令。宿主用 Win32
  `ReadFile` 直接从标准输入取命令行（见 `host/StdinLineReader.cs`），不走 .NET 的
  `Console.OpenStandardInput()`——在 WinForms 进程里后者被 QProcess 的匿名管道拉起时
  读不到数据，所有命令都会被静默丢弃。
* **宿主程序编译失败**：`%LOCALAPPDATA%` 不可写时会自动改用工作目录下的
  `cache/desktop_clone`；两处都不可写才报错，错误信息里会带上 csc 的原始输出。
* **子会话里启动程序失败**：子会话尚未建立时会这样，等画面显示出来（状态变成「已连接」）再启动。
  程序通过任务计划程序 COM（`Schedule.Service`）注册一个临时任务，再用 `RunEx` 的
  `TASK_RUN_USE_SESSION_ID` 直接丢进子会话运行（和 BetterGenshinImpact 一致）——
  只用 `schtasks /Run` 的话进程会落在当前会话而不是子会话。任务用完立刻删掉。
* **注销后程序还在**：注销只是结束子会话，「断开」才是保留会话、只关窗口。
* **只能在 Windows 上用**：该功能依赖 Windows 的 WTS 与 RDP 组件，其他平台会被禁用。