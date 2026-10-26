using System;
using System.ComponentModel;
using System.Drawing;
using System.IO;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;
using WinFormsTimer = System.Windows.Forms.Timer;

namespace OneDragonScriptChainer.DesktopClone
{
    /// <summary>
    /// 桌面分身宿主窗口。
    ///
    /// 它是个无边框的顶层窗口，位置和大小由 Python（PySide6）端每 200ms 同步过来，
    /// 正好盖在界面上的「屏幕」区域上方；窗口的所有者设成主窗口，
    /// 所以它会跟着主窗口一起移动/最小化，也不会被主窗口盖住。
    /// 窗口内部嵌着远程桌面 ActiveX 控件，该控件连到本机的子会话，
    /// 于是子会话的桌面就显示在千机链的界面里。
    ///
    /// 注意：不要把窗口 SetParent 到另一个进程的控件上——跨进程子窗口在
    /// 不同 DPI 感知方式下会被系统挡住，表现就是「连上了但一片黑」。
    /// </summary>
    internal sealed class HostForm : Form
    {
        private const int DefaultDesktopWidth = 1920;
        private const int DefaultDesktopHeight = 1080;

        /// <summary>设置环境变量 ONEDRAGON_DESKTOP_CLONE_DEBUG=1 后把命令往来打到 stderr。</summary>
        private static readonly bool DebugInput =
            Environment.GetEnvironmentVariable("ONEDRAGON_DESKTOP_CLONE_DEBUG") == "1";

        private readonly IntPtr _ownerHandle;
        private readonly RdpClient _rdp;
        private readonly ProtocolWriter _writer;

        /// <summary>未连接时画在画面上的提示文字，告诉用户现在是哪种状态。</summary>
        private string _statusText = "桌面分身尚未连接\n点「连接」开始";
        private readonly Font _statusFont = new Font("Microsoft YaHei UI", 11f, FontStyle.Regular);

        private string _statusError = string.Empty;
        private readonly WinFormsTimer _layoutTimer;
        private readonly WinFormsTimer _stateTimer;

        /// <summary>
        /// 断开/注销之后连着扫几遍地光标。控件是在断开流程里陆续把捕获和裁剪放掉的，
        /// 只收拾一次不够——画面窗口挪走之后它可能又裁一次，光标就又被钉住了。
        /// </summary>
        private readonly WinFormsTimer _cursorSweepTimer;

        private int _cursorSweepTicks;

        /// <summary>界面端发来 bounds 之前先把窗口停在屏幕外，避免在屏幕角落闪一下。</summary>
        private static readonly Point OffScreenLocation = new Point(-32000, -32000);

        private int _desktopWidth = DefaultDesktopWidth;
        private int _desktopHeight = DefaultDesktopHeight;
        private bool _lowBandwidth;

        private bool _smartSizing = true;
        private bool _audioMuted;
        private bool _sendSystemShortcuts = true;
        private bool _inputBlocked;

        private int _lastState = -1;
        private uint _lastSessionId;
        private bool _connectRequested;

        /// <summary>未连接时把远程桌面控件整体挪出可视区，否则它会用黑色子窗口盖住提示文字。</summary>
        private bool _rdpOffscreen;

        /// <summary>画面是否已经嵌进界面窗口（跨进程子窗口）。</summary>
        private bool _embedded;

        private IntPtr _embedParent;

        private int _embedWidth;

        private int _embedHeight;

        /// <summary>切换分辨率必须断开重连，这里记住重连目标尺寸。</summary>
        private int _pendingDesktopWidth;

        private int _pendingDesktopHeight;

        private int _lastBoundsX;
        private int _lastBoundsY;
        private int _lastBoundsWidth;
        private int _lastBoundsHeight;

        internal HostForm(IntPtr ownerHandle)
        {
            _ownerHandle = ownerHandle;
            _writer = new ProtocolWriter();

            FormBorderStyle = FormBorderStyle.None;
            StartPosition = FormStartPosition.Manual;
            ShowInTaskbar = false;
            MinimizeBox = false;
            MaximizeBox = false;
            KeyPreview = false;
            Text = "OneDragon 桌面分身";
            BackColor = Color.Black;
            Location = OffScreenLocation;

            _rdp = new RdpClient();
            _rdp.OwnerWindow = ownerHandle;
            Controls.Add(_rdp);

            ClientSize = new Size(DefaultDesktopWidth / 2, DefaultDesktopHeight / 2);

            _layoutTimer = new WinFormsTimer();
            _layoutTimer.Interval = 250;
            _layoutTimer.Tick += OnLayoutTick;

            _stateTimer = new WinFormsTimer();
            _stateTimer.Interval = 500;
            _stateTimer.Tick += OnStateTick;

            _cursorSweepTimer = new WinFormsTimer();
            _cursorSweepTimer.Interval = 100;
            _cursorSweepTimer.Tick += OnCursorSweepTick;
        }

        protected override void OnHandleCreated(EventArgs e)
        {
            base.OnHandleCreated(e);
            if (_ownerHandle != IntPtr.Zero && NativeMethods.IsWindow(_ownerHandle))
            {
                NativeMethods.SetOwner(Handle, _ownerHandle);
            }

            _stateTimer.Start();
            _writer.Write("ready " + Handle.ToInt64().ToString());
            // 句柄就绪后才读命令，否则 BeginInvoke 会因为窗口还没创建而失败
            StartInputLoop();
        }

        protected override void OnResize(EventArgs e)
        {
            base.OnResize(e);
            LayoutRdp();
        }

        /// <summary>画面窗口不进任务栏的 Alt+Tab 列表，但允许被激活（否则画面里敲不了键盘）。</summary>
        protected override CreateParams CreateParams
        {
            get
            {
                CreateParams parameters = base.CreateParams;
                parameters.ExStyle |= NativeMethods.WsExToolWindow;
                return parameters;
            }
        }

        private void OnLayoutTick(object sender, EventArgs e)
        {
            if (_embedded)
            {
                return;
            }

            LayoutRdp();
        }

        /// <summary>
        /// 断开/注销之后把本地光标收回来，并在接下来两秒里重复确认几次。
        ///
        /// 控件连着会话时会把光标裁剪在自己的画面范围内，断开时这条裁剪不一定马上解开；
        /// 而且未连接时画面窗口会被挪到屏幕外缩成 1x1，裁剪范围跟着变成一个屏幕外的点，
        /// 本地鼠标就彻底推不动了（用户报的「点击注销分身后鼠标无法移动」）。
        /// </summary>
        private void BeginCursorFreeSweep()
        {
            if (_rdp == null)
            {
                return;
            }

            _rdp.FreeLocalCursor();
            _cursorSweepTicks = 20;
            _cursorSweepTimer.Stop();
            _cursorSweepTimer.Start();
        }

        private void OnCursorSweepTick(object sender, EventArgs e)
        {
            _rdp.FreeLocalCursor();
            _cursorSweepTicks--;
            if (_cursorSweepTicks <= 0)
            {
                _cursorSweepTimer.Stop();
            }
        }

        /// <summary>控件不靠 Dock，按客户区手工铺一次，避免画面只画在一小块区域。</summary>
        private void LayoutRdp()
        {
            // 基类在构造阶段就会触发 OnResize，那时控件还没建出来
            if (_rdp == null || _rdpOffscreen)
            {
                return;
            }

            if (_rdp.Width != ClientSize.Width || _rdp.Height != ClientSize.Height)
            {
                _rdp.SetBounds(0, 0, ClientSize.Width, ClientSize.Height);
            }
        }

        /// <summary>未连接时在窗口上直接画提示文字（子控件会被远程桌面控件自己的窗口盖住）。</summary>
        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            if (_rdp != null && _rdp.Visible)
            {
                return;
            }

            // 逐行手算居中位置（GDI+ 的 DrawString 矩形重载在这台机上有笔画不出来的情况）
            string[] lines = _statusText.Split('\n');
            Font font = SystemFonts.MessageBoxFont;
            float lineHeight = font.GetHeight(e.Graphics) * 1.3f;
            float y = (ClientSize.Height - (lineHeight * lines.Length)) / 2f;
            using (var brush = new SolidBrush(Color.Gainsboro))
            {
                foreach (string line in lines)
                {
                    float width = e.Graphics.MeasureString(line, font).Width;
                    e.Graphics.DrawString(line, font, brush, (ClientSize.Width - width) / 2f, y);
                    y += lineHeight;
                }
            }
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            _stateTimer.Stop();
            _cursorSweepTimer.Stop();
            try
            {
                _rdp.Disconnect();
            }
            catch (Exception)
            {
                // 关闭阶段控件可能已经释放，忽略即可
            }

            _writer.Write("bye");
            base.OnFormClosing(e);
        }

        /// <summary>按当前状态刷新画面上的提示文字。</summary>
        private void RefreshOverlay(int state)
        {
            if (state == 1)
            {
                _rdpOffscreen = false;
                if (!_rdp.Visible)
                {
                    _rdp.Visible = true;
                }

                LayoutRdp();
                _rdp.Refresh();
                Invalidate();
                return;
            }

            // 未连接时把远程桌面控件整个藏起来并挪出可视区：它内部还有自己的子窗口，
            // 只把提示文字提上来盖不住那些子窗口（看起来就是一片黑）
            _rdpOffscreen = true;
            if (_rdp.Visible)
            {
                _rdp.Visible = false;
            }

            if (_rdp.Left > -1000 || _rdp.Top > -1000)
            {
                _rdp.SetBounds(-32000, -32000, 1, 1);
            }

            // 画面挪走之后控件可能把光标裁剪范围一起带成屏幕外的一个点，得把光标收回来
            BeginCursorFreeSweep();

            string text;
            if (state == 2 || state == 4)
            {
                text = "正在连接桌面分身…\n首次连接可能需要十几秒";
            }
            else if (!string.IsNullOrEmpty(_statusError))
            {
                text = _statusError;
            }
            else
            {
                text = "桌面分身尚未连接\n点「连接」开始";
            }

            if (_statusText != text)
            {
                _statusText = text;
            }

            Invalidate();
        }

        private void OnStateTick(object sender, EventArgs e)
        {
            int state = _rdp.ConnectedState;
            if (state != _lastState)
            {
                // 从「已连接/连接中」掉回未连接，说明连接失败或被断开
                if (state == 0 && _lastState > 0 && _connectRequested)
                {
                    string reason = _rdp.DescribeDisconnectReason();
                    if (string.IsNullOrEmpty(reason))
                    {
                        reason =
                            "桌面分身连接失败。\n"
                            + "请确认：\n"
                            + "1. 本程序以管理员身份运行；\n"
                            + "2. 已在「设置 → 系统 → 远程桌面」中开启远程桌面；\n"
                            + "3. 系统里没有运行 RDPWrap 之类的远程桌面补丁。";
                    }

                    _writer.Error(reason);
                    _statusError = reason;
                }

                _lastState = state;
                _writer.Write("state " + state.ToString());
                RefreshOverlay(state);
            }

            if (state == 0 && _pendingDesktopWidth > 0)
            {
                // 断开完成：用新分辨率重新连一次
                int width = _pendingDesktopWidth;
                int height = _pendingDesktopHeight;
                _pendingDesktopWidth = 0;
                _pendingDesktopHeight = 0;
                Connect(width, height);
                return;
            }

            if (state != 1)
            {
                return;
            }

            _connectRequested = false;
            uint sessionId = NativeMethods.TryGetChildSessionId();
            if (sessionId != _lastSessionId)
            {
                _lastSessionId = sessionId;
                _writer.Write("session " + sessionId.ToString());
            }

            _rdp.SetInputEnabled(!_inputBlocked);
        }

        private void StartInputLoop()
        {
            var thread = new Thread(InputLoop)
            {
                IsBackground = true,
                Name = "desktop-clone-input"
            };
            thread.Start();
        }

        private void InputLoop()
        {
            try
            {
                var reader = new StdinLineReader();
                if (DebugInput)
                {
                    Console.Error.WriteLine("input-loop-started");
                    Console.Error.Flush();
                }

                string line;
                while ((line = reader.ReadLine()) != null)
                {
                    string command = line.Trim();
                    if (command.Length == 0)
                    {
                        continue;
                    }

                    if (DebugInput)
                    {
                        Console.Error.WriteLine("cmd " + command);
                        Console.Error.Flush();
                    }

                    try
                    {
                        BeginInvoke(new Action<string>(HandleCommand), command);
                    }
                    catch (InvalidOperationException)
                    {
                        // 窗口已销毁
                        return;
                    }
                }

                if (DebugInput)
                {
                    Console.Error.WriteLine("input-loop-eof");
                    Console.Error.Flush();
                }
            }
            catch (Exception ex)
            {
                if (DebugInput)
                {
                    Console.Error.WriteLine("input-loop-error " + ex.Message);
                    Console.Error.Flush();
                }
            }

            // 上级进程关闭了管道：宿主随之退出
            try
            {
                BeginInvoke(new Action(Close));
            }
            catch (Exception)
            {
                // 窗口已销毁
            }
        }

        private void HandleCommand(string command)
        {
            string[] parts = command.Split(new[] { ' ' }, 6);
            string verb = parts[0].ToLowerInvariant();
            try
            {
                switch (verb)
                {
                    case "connect":
                        Connect(ParseInt(parts, 1, DefaultDesktopWidth), ParseInt(parts, 2, DefaultDesktopHeight));
                        break;
                    case "disconnect":
                        _rdp.Disconnect();
                        _statusError = string.Empty;
                        RefreshOverlay(0);
                        break;
                    case "logoff":
                        LogoffChildSession();
                        break;
                    case "launch":
                        Launch(parts);
                        break;
                    case "shortcut":
                        SendShortcut(parts.Length > 1 ? parts[1] : string.Empty);
                        break;
                    case "smartsizing":
                        _smartSizing = ParseBool(parts, 1);
                        _rdp.SetSmartSizing(_smartSizing);
                        break;
                    case "audiomute":
                        _audioMuted = ParseBool(parts, 1);
                        _writer.Message("声音重定向设置将在下次连接时生效");
                        break;
                    case "syskeys":
                        _sendSystemShortcuts = ParseBool(parts, 1);
                        _writer.Message("系统快捷键设置将在下次连接时生效");
                        break;
                    case "input":
                        _inputBlocked = !ParseBool(parts, 1);
                        _rdp.SetInputEnabled(!_inputBlocked);
                        break;
                    case "embed":
                        // embed 句柄 宽 高：parts[2] 是宽，别写成 parts[3]
                        EmbedWindow(
                            ParseHandle(parts, 1),
                            ParseInt(parts, 2, DefaultDesktopWidth / 2),
                            ParseInt(parts, 3, DefaultDesktopHeight / 2));
                        break;
                    case "resize":
                        ResizeEmbedded(
                            ParseInt(parts, 1, DefaultDesktopWidth / 2),
                            ParseInt(parts, 2, DefaultDesktopHeight / 2));
                        break;
                    case "resolution":
                        ChangeResolution(
                            ParseInt(parts, 1, DefaultDesktopWidth),
                            ParseInt(parts, 2, DefaultDesktopHeight));
                        break;
                    case "bounds":
                        MoveOverlay(
                            ParseInt(parts, 1, 0),
                            ParseInt(parts, 2, 0),
                            ParseInt(parts, 3, DefaultDesktopWidth / 2),
                            ParseInt(parts, 4, DefaultDesktopHeight / 2));
                        break;
                    case "show":
                        ShowOverlay();
                        break;
                    case "hide":
                        HideOverlay();
                        break;
                    case "topmost":
                        NativeMethods.SetTopMost(Handle, ParseBool(parts, 1));
                        break;
                    case "lowbw":
                        _lowBandwidth = ParseBool(parts, 1);
                        break;
                    case "visible":
                        SetEmbeddedVisible(ParseBool(parts, 1));
                        break;
                    case "status":
                        ReportStatus();
                        break;
                    case "diag":
                        ReportDiagnostics();
                        break;
                    case "quit":
                        Close();
                        break;
                    default:
                        _writer.Error("未知命令：" + verb);
                        break;
                }
            }
            catch (Exception ex)
            {
                _writer.Error("命令 " + verb + " 失败：" + ex.Message);
            }
        }


        /// <summary>按界面端给的屏幕坐标摆放窗口。</summary>
        private void MoveOverlay(int x, int y, int width, int height)
        {
            // 用 WinForms 的 Bounds 而不是 SetWindowPos：本进程是按监视器 DPI 感知的，
            // 直接塞物理像素会被窗口管理器按缩放比例放大，窗口比请求的大一大圈
            Bounds = new Rectangle(x, y, Math.Max(1, width), Math.Max(1, height));
            _lastBoundsX = x;
            _lastBoundsY = y;
            _lastBoundsWidth = width;
            _lastBoundsHeight = height;
            LayoutRdp();

            // 只摆位置，不动 Z 序：Z 序由界面端用置顶开关控制，
            // 这里如果每次都抬一次，会把别的程序窗口也盖住。
        }

        /// <summary>真正显示窗口（第一次显示会顺带把 WinForms 的 Visible 置上）。</summary>
        private void SetEmbeddedVisible(bool visible)
        {
            if (_embedded)
            {
                // 嵌在页面里的时候，页面切走要把窗口一起藏起来，
                // 否则它只是窗口的「子窗口」，Qt 藏了控件它也还画在原处
                NativeMethods.ShowWindow(Handle, visible ? NativeMethods.SwShow : NativeMethods.SwHide);
                if (!visible)
                {
                    // 画面藏起来了，控件留在系统里的光标裁剪也得一起收掉，不然鼠标出不了那块区域
                    BeginCursorFreeSweep();
                }

                return;
            }

            if (visible)
            {
                ShowOverlay();
            }
            else
            {
                HideOverlay();
            }
        }

        private void ShowOverlay()
        {
            if (_embedded)
            {
                return;
            }

            NativeMethods.ShowNoActivate(Handle);
            _layoutTimer.Start();
            LayoutRdp();
            NativeMethods.RaiseNoActivate(Handle);
        }

        /// <summary>隐藏窗口（切到别的页面、最小化主窗口时用）。</summary>
        private void HideOverlay()
        {
            if (_embedded)
            {
                return;
            }

            _layoutTimer.Stop();
            NativeMethods.HideWindow(Handle);
            // 窗口藏了，控件留下的光标裁剪同样要收回，否则鼠标被夹在已经看不见的画面里
            BeginCursorFreeSweep();
        }

        /// <summary>把窗口与远程桌面控件的实际状态回报给界面端，便于排查黑屏。</summary>
        private void ReportDiagnostics()
        {
            var builder = new StringBuilder();
            builder.Append("窗口可见=").Append(NativeMethods.IsWindowVisible(Handle) ? "1" : "0");
            builder.Append(" 控件可见=").Append(_rdp.Visible ? "1" : "0");
            builder.Append(" 控件尺寸=").Append(_rdp.Width).Append("x").Append(_rdp.Height);
            builder.Append(" 客户区=").Append(ClientSize.Width).Append("x").Append(ClientSize.Height);
            builder.Append(" 句柄=").Append(Handle.ToInt64().ToString());
            builder.Append(" 位置=").Append(_lastBoundsX).Append(",").Append(_lastBoundsY);
            builder.Append(" 尺寸=").Append(_lastBoundsWidth).Append("x").Append(_lastBoundsHeight);
            builder.Append(" 桌面=").Append(_desktopWidth).Append("x").Append(_desktopHeight);
            builder.Append(" 状态=").Append(_rdp.ConnectedState.ToString());
            builder.Append(" 会话=").Append(NativeMethods.TryGetChildSessionId().ToString());
            builder.Append(" 文字长=").Append(_statusText.Length);
            builder.Append(" 窗口矩形=").Append(NativeMethods.DescribeWindowRect(Handle));
            builder.Append(" 置顶=").Append(NativeMethods.IsTopMost(Handle) ? "1" : "0");
            builder.Append(" 拥有者=").Append(NativeMethods.GetOwner(Handle).ToInt64().ToString());
            builder.Append(" 主窗口=").Append(_ownerHandle.ToInt64().ToString());
            builder.Append(" 嵌入=").Append(_embedded ? "1" : "0");
            builder.Append(" 父窗口=").Append(_embedParent.ToInt64().ToString());
            builder.Append(" 期望=").Append(_embedWidth).Append("x").Append(_embedHeight);
            builder.Append(" 父客户区=").Append(NativeMethods.DescribeClientRect(_embedParent));
            builder.Append(" 样式=").Append(NativeMethods.DescribeStyle(Handle));
            _writer.Message(builder.ToString());
        }

        private void Connect(int width, int height)
        {
            _desktopWidth = ClampDimension(width);
            _desktopHeight = ClampDimension(height);

            // 重复点「连接」时先把上一次的控件状态收回来，避免 RDP 控件报「已经在连接中」
            if (_rdp.ConnectedState != 0)
            {
                _rdp.Disconnect();
            }

            try
            {
                EnsureChildSessionsEnabled();

                int port = ReadRdpPort();
                _statusError = string.Empty;
                _writer.Message("正在连接桌面分身（会话端口 " + port + "）…");
                _connectRequested = true;
                RefreshOverlay(2);
                _rdp.ConnectToChildSession(
                    _desktopWidth,
                    _desktopHeight,
                    _smartSizing,
                    _sendSystemShortcuts,
                    _audioMuted,
                    port,
                    _lowBandwidth);
            }
            catch (Exception ex)
            {
                _connectRequested = false;
                _statusError =
                    "连接桌面分身失败：" + DescribeException(ex) + "\n请确认：\n"
                    + "1. 本程序已以管理员身份运行；\n"
                    + "2. 账户设置有登录密码（只能用 Windows Hello 登录的账户无法启用子会话）。";
                RefreshOverlay(0);
                throw new InvalidOperationException(_statusError, ex);
            }
        }

        private static string DescribeException(Exception ex)
        {
            var builder = new StringBuilder();
            Exception current = ex;
            while (current != null)
            {
                if (builder.Length > 0)
                {
                    builder.Append(" <- ");
                }

                builder.Append(current.Message);
                builder.Append("（0x");
                builder.Append(current.HResult.ToString("X8"));
                builder.Append("）");
                current = current.InnerException;
            }

            return builder.ToString();
        }

        private void Launch(string[] parts)
        {
            uint sessionId = NativeMethods.TryGetChildSessionId();
            if (sessionId == 0)
            {
                throw new InvalidOperationException("桌面分身尚未建立子会话，无法启动程序");
            }

            string exePath = ProtocolWriter.Decode(Get(parts, 1));
            string arguments = ProtocolWriter.Decode(Get(parts, 2));
            string workDirectory = ProtocolWriter.Decode(Get(parts, 3));

            // 建计划任务 + 等进程出现会占用几秒，先让界面上有个提示
            _writer.Message("正在桌面分身中启动 " + Path.GetFileName(exePath) + "…");

            int pid = SessionLauncher.Launch(sessionId, exePath, arguments, workDirectory);
            _writer.Write("launched " + pid.ToString());
            if (pid == 0)
            {
                _writer.Message("已请求在桌面分身中启动 " + Path.GetFileName(exePath) + "，但未找到进程，请查看分身桌面");
            }
            else
            {
                _writer.Message("已在桌面分身中启动 " + Path.GetFileName(exePath) + "（PID " + pid + "）");
            }
        }

        private void LogoffChildSession()
        {
            uint sessionId = NativeMethods.TryGetChildSessionId();
            if (sessionId == 0)
            {
                _writer.Message("当前没有正在运行的桌面分身会话");
                return;
            }

            _rdp.Disconnect();
            // 注销会把子会话一起关掉，控件这边的收尾更乱，光标得多扫几遍才稳
            BeginCursorFreeSweep();
            bool ok = NativeMethods.WTSLogoffSession(IntPtr.Zero, sessionId, true);
            if (!ok)
            {
                throw new Win32Exception(Marshal_GetLastWin32Error(), "注销桌面分身会话失败");
            }

            _lastSessionId = 0;
            _writer.Write("session 0");
            _writer.Message("桌面分身会话 " + sessionId + " 已注销");
        }

        private void SendShortcut(string name)
        {
            DesktopShortcut shortcut;
            if (string.Equals(name, "wind", StringComparison.OrdinalIgnoreCase))
            {
                shortcut = DesktopShortcut.ShowDesktop;
            }
            else if (string.Equals(name, "wintab", StringComparison.OrdinalIgnoreCase))
            {
                shortcut = DesktopShortcut.TaskView;
            }
            else
            {
                throw new ArgumentException("不支持的快捷键：" + name);
            }

            _rdp.SendShortcut(shortcut);
            _writer.Message(shortcut == DesktopShortcut.ShowDesktop ? "已发送 Win+D" : "已发送 Win+Tab");
        }

        /// <summary>把画面窗口嵌进界面给的父窗口（父窗口属于界面进程）。</summary>
        private void EmbedWindow(IntPtr parent, int width, int height)
        {
            if (parent == IntPtr.Zero || !NativeMethods.IsWindow(parent))
            {
                _writer.Message("界面给的画面容器句柄无效，改用独立窗口显示");
                return;
            }

            if (_embedded && _embedParent == parent)
            {
                ResizeEmbedded(width, height);
                return;
            }

            // 只走原生 SetParent：一旦把 TopLevel 置成 false，WinForms 会接管
            // 父子关系并在下一次布局时用自己的 Bounds 覆盖我们设的大小
            _layoutTimer.Stop();
            _embedParent = parent;
            _embedWidth = width;
            _embedHeight = height;
            _embedded = WindowHosting.Embed(Handle, parent, width, height);
            if (!_embedded)
            {
                TopLevel = true;
                _embedParent = IntPtr.Zero;
                _writer.Message("未能把画面嵌入界面，改用独立窗口显示");
                return;
            }

            // 发快捷键时要把本程序切到前台，用父窗口的顶层窗口（主界面）更稳
            _rdp.OwnerWindow = WindowHosting.RootWindow(parent);
            LayoutRdp();
            Invalidate();
            _writer.Write("embedded 1");
            _writer.Message("画面已嵌入界面（" + width + "x" + height + "）");
        }

        /// <summary>界面缩放后同步子窗口尺寸。</summary>
        private void ResizeEmbedded(int width, int height)
        {
            if (!_embedded)
            {
                return;
            }

            WindowHosting.SetChildBounds(Handle, 0, 0, width, height);
            LayoutRdp();
        }

        /// <summary>
        /// 切换分身分辨率：远程桌面尺寸只在连接时确定，所以要断开后用新尺寸重连。
        /// </summary>
        private void ChangeResolution(int width, int height)
        {
            int newWidth = ClampDimension(width);
            int newHeight = ClampDimension(height);
            if (newWidth == _desktopWidth && newHeight == _desktopHeight)
            {
                _writer.Message("分辨率没有变化：" + newWidth + "x" + newHeight);
                return;
            }

            if (_rdp.ConnectedState == 0)
            {
                _desktopWidth = newWidth;
                _desktopHeight = newHeight;
                _writer.Message("分辨率已记住，下次连接时生效（" + newWidth + "x" + newHeight + "）");
                return;
            }

            _writer.Message("正在切换分身分辨率到 " + newWidth + "x" + newHeight + "…");
            _pendingDesktopWidth = newWidth;
            _pendingDesktopHeight = newHeight;
            _rdp.Disconnect();
        }

        private static IntPtr ParseHandle(string[] parts, int index)
        {
            if (parts.Length <= index)
            {
                return IntPtr.Zero;
            }

            long value;
            return long.TryParse(parts[index], out value) ? new IntPtr(value) : IntPtr.Zero;
        }

        private void ReportStatus()
        {
            uint sessionId = NativeMethods.TryGetChildSessionId();
            string station = sessionId == 0 ? string.Empty : NativeMethods.QueryWinStationName(sessionId);
            _writer.Write("state " + _rdp.ConnectedState.ToString());
            _writer.Write("session " + sessionId.ToString());
            _writer.Message(
                "控件状态 " + _rdp.ConnectedState
                + "，子会话 " + (sessionId == 0 ? "无" : sessionId.ToString())
                + (string.IsNullOrEmpty(station) ? string.Empty : "（" + station + "）")
                + "，端口 " + ReadRdpPort().ToString());
        }

        private static void EnsureChildSessionsEnabled()
        {
            int enabled;
            if (NativeMethods.WTSIsChildSessionsEnabled(out enabled) && enabled != 0)
            {
                return;
            }

            if (!NativeMethods.WTSEnableChildSessions(true))
            {
                throw new Win32Exception(Marshal_GetLastWin32Error(), "开启 Windows 子会话失败");
            }
        }

        private static int ReadRdpPort()
        {
            try
            {
                using (RegistryKey baseKey = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64))
                {
                    if (baseKey != null)
                    {
                        using (RegistryKey key = baseKey.OpenSubKey(
                            @"SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp"))
                        {
                            if (key != null)
                            {
                                object value = key.GetValue("PortNumber");
                                if (value != null)
                                {
                                    return Convert.ToInt32(value);
                                }
                            }
                        }
                    }
                }
            }
            catch (Exception)
            {
                // 读不到就用默认端口
            }

            return 3389;
        }

        private static int Marshal_GetLastWin32Error()
        {
            return System.Runtime.InteropServices.Marshal.GetLastWin32Error();
        }

        private static int ClampDimension(int value)
        {
            if (value < 200)
            {
                return 200;
            }

            return value > 8192 ? 8192 : value;
        }

        private static int ParseInt(string[] parts, int index, int fallback)
        {
            if (parts.Length <= index)
            {
                return fallback;
            }

            int value;
            return int.TryParse(parts[index], out value) ? value : fallback;
        }

        private static bool ParseBool(string[] parts, int index)
        {
            if (parts.Length <= index)
            {
                return false;
            }

            string value = parts[index];
            return value == "1"
                || string.Equals(value, "true", StringComparison.OrdinalIgnoreCase)
                || string.Equals(value, "on", StringComparison.OrdinalIgnoreCase);
        }

        private static string Get(string[] parts, int index)
        {
            return parts.Length <= index ? string.Empty : parts[index];
        }
    }
}