using System;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Windows.Forms;

namespace OneDragonScriptChainer.DesktopClone
{
    /// <summary>
    /// 承载 Windows 远程桌面 ActiveX 控件（MsRdpClient10, NotSafeForScripting）。
    /// 该控件连接本机的「子会话」桌面，从而把「桌面分身」画面嵌进本程序的窗口。
    /// 机制与 BetterGenshinImpact 的 RdpActiveXHost 相同。
    /// </summary>
    internal sealed class RdpClient : AxHost, IMessageFilter
    {
        /// <summary>MsRdpClient10 NotSafeForScripting 的 CLSID，Windows 10 及以后可用。</summary>
        private const string RdpClientClassId = "A0C63C30-F08D-4AB4-907C-34905D770C7D";

        private const short VariantTrue = -1;
        private const short VariantFalse = 0;

        // 观看模式要拦的消息：鼠标移动、按键、滚轮和原始输入。点击类消息顺带一起拦，
        // 不然画面里点一下还是会把窗口激活或给远程会话送去一次点击。
        private const int WmInput = 0x00FF;
        private const int WmKeyDown = 0x0100;
        private const int WmKeyUp = 0x0101;
        private const int WmChar = 0x0102;
        private const int WmSysKeyDown = 0x0104;
        private const int WmSysKeyUp = 0x0105;
        private const int WmSysChar = 0x0106;
        private const int WmMouseMove = 0x0200;
        private const int WmLeftButtonDown = 0x0201;
        private const int WmLeftButtonUp = 0x0202;
        private const int WmLeftButtonDoubleClick = 0x0203;
        private const int WmRightButtonDown = 0x0204;
        private const int WmRightButtonUp = 0x0205;
        private const int WmRightButtonDoubleClick = 0x0206;
        private const int WmMiddleButtonDown = 0x0207;
        private const int WmMiddleButtonUp = 0x0208;
        private const int WmMiddleButtonDoubleClick = 0x0209;
        private const int WmMouseWheel = 0x020A;
        private const int WmXButtonDown = 0x020B;
        private const int WmXButtonUp = 0x020C;
        private const int WmXButtonDoubleClick = 0x020D;
        private const int WmMouseHWheel = 0x020E;

        private const int KeyboardHookModeLocal = 0;
        private const int KeyboardHookModeRemote = 1;
        private const int AudioRedirectionModeLocal = 0;
        private const int AudioRedirectionModeRemote = 2;

        private IntPtr _inputCaptureWindow;

        private bool _inputBlocked;

        private readonly CursorGuard _cursorGuard;

        /// <summary>宿主窗口（切前台、把焦点拉进分身时用）。</summary>
        internal IntPtr OwnerWindow { get; set; }

        internal RdpClient()
            : base(RdpClientClassId)
        {
            Dock = DockStyle.Fill;
            BackColor = System.Drawing.Color.Black;
            TabStop = false;
            // 观看模式不能靠控件自己配合，得在消息派发之前拦下来，见 PreFilterMessage
            Application.AddMessageFilter(this);
            // 观看模式下别让远程光标把本地鼠标搬走或夹住，见 CursorGuard
            _cursorGuard = new CursorGuard();
        }

        protected override void OnHandleCreated(EventArgs e)
        {
            base.OnHandleCreated(e);
            // 只有本控件的捕获和裁剪才归护栏管，句柄出来之后再告诉它
            _cursorGuard.AttachTo(Handle);
        }

        /// <summary>远程桌面控件的连接状态：0 未连接、1 已连接、其它见 IMsTscAx。</summary>
        internal int ConnectedState
        {
            get
            {
                try
                {
                    object ocx = Ocx();
                    object value = GetProperty(ocx, "Connected");
                    return value == null ? 0 : Convert.ToInt32(value);
                }
                catch (Exception)
                {
                    return 0;
                }
            }
        }

        /// <summary>向本机子会话发起的桌面连接。</summary>
        /// <param name="width">虚拟桌面宽度。</param>
        /// <param name="height">虚拟桌面高度。</param>
        /// <param name="smartSizing">是否让画面随控件大小缩放。</param>
        /// <param name="sendSystemShortcuts">键盘是否发送 Windows 系统快捷键。</param>
        /// <param name="audioMuted">是否禁用声音重定向。</param>
        /// <param name="rdpPort">远程桌面端口，一般取注册表 RDP-Tcp 的 PortNumber。</param>
        /// <param name="lowBandwidth">省性能模式：16 位色 + 关掉壁纸/主题等花哨效果。</param>
        internal void ConnectToChildSession(
            int width,
            int height,
            bool smartSizing,
            bool sendSystemShortcuts,
            bool audioMuted,
            int rdpPort,
            bool lowBandwidth)
        {
            object ocx = Ocx();

            SetProperty(ocx, "Server", "localhost");
            SetProperty(ocx, "DesktopWidth", ClampDesktopDimension(width));
            SetProperty(ocx, "DesktopHeight", ClampDesktopDimension(height));
            // 4K 下单是 32 位色的位图就够吃 CPU 了，省性能模式退到 16 位色
            SetProperty(ocx, "ColorDepth", lowBandwidth ? 16 : 32);
            SetProperty(ocx, "ConnectingText", "正在连接桌面分身…");
            SetProperty(ocx, "DisconnectedText", "桌面分身已断开连接");

            object securedSettings = GetProperty(ocx, "SecuredSettings2");
            SetProperty(
                securedSettings,
                "KeyboardHookMode",
                sendSystemShortcuts ? KeyboardHookModeRemote : KeyboardHookModeLocal);
            SetProperty(
                securedSettings,
                "AudioRedirectionMode",
                audioMuted ? AudioRedirectionModeRemote : AudioRedirectionModeLocal);

            object advancedSettings = GetProperty(ocx, "AdvancedSettings7");
            if (rdpPort > 0)
            {
                SetProperty(advancedSettings, "RDPPort", rdpPort);
            }

            SetProperty(advancedSettings, "EnableCredSspSupport", true);
            SetProperty(advancedSettings, "EnableWindowsKey", sendSystemShortcuts ? 1 : 0);
            SetProperty(advancedSettings, "SmartSizing", smartSizing);

            if (lowBandwidth)
            {
                TrySetProperty(advancedSettings, "DisableWallpaper", true);
                TrySetProperty(advancedSettings, "DisableFullWindowDrag", true);
                TrySetProperty(advancedSettings, "DisableMenuAnims", true);
                TrySetProperty(advancedSettings, "DisableThemes", true);
                TrySetProperty(advancedSettings, "BitmapPersistence", 0);
            }

            // 子会话连接需要 IMsRdpExtendedSettings 上打开这两个开关，否则控件只会连上当前桌面
            var extendedSettings = (IMsRdpExtendedSettings)ocx;
            SetExtendedProperty(extendedSettings, "EnableZoom", true);
            SetExtendedProperty(extendedSettings, "ConnectToChildSession", true);

            InvokeMethod(ocx, "Connect");
        }

        internal void Disconnect()
        {
            if (!IsHandleCreated)
            {
                return;
            }

            try
            {
                InvokeMethod(Ocx(), "Disconnect");
            }
            catch (Exception)
            {
                // 控件可能已经释放，忽略
            }
        }

        /// <summary>动态修改画面自适应缩放（无需重连）。</summary>
        internal void SetSmartSizing(bool enabled)
        {
            try
            {
                object advancedSettings = GetProperty(Ocx(), "AdvancedSettings7");
                SetProperty(advancedSettings, "SmartSizing", enabled);
            }
            catch (Exception)
            {
                // 控件尚未初始化时忽略
            }
        }

        /// <summary>
        /// 发送组合键到子会话桌面。
        /// 目前只支持 Win+D（显示桌面）与 Win+Tab（任务视图），与 BetterGI 一致。
        /// </summary>
        internal void SendShortcut(DesktopShortcut shortcut)
        {
            if (ConnectedState != 1)
            {
                throw new InvalidOperationException("桌面分身尚未连接，无法发送快捷键");
            }

            int[] scanCodes;
            bool[] keyUps;
            bool[] extended;
            switch (shortcut)
            {
                case DesktopShortcut.ShowDesktop:
                    // LWIN + D
                    scanCodes = new[] { 0x5B, 0x20, 0x20, 0x5B };
                    keyUps = new[] { false, false, true, true };
                    extended = new[] { true, false, false, true };
                    break;
                case DesktopShortcut.TaskView:
                    // LWIN + TAB
                    scanCodes = new[] { 0x5B, 0x0F, 0x0F, 0x5B };
                    keyUps = new[] { false, false, true, true };
                    extended = new[] { true, false, false, true };
                    break;
                default:
                    throw new ArgumentOutOfRangeException("shortcut");
            }

            IntPtr inputWindow = FindInputCaptureWindow();
            bool restored = false;
            if (_inputBlocked && inputWindow != IntPtr.Zero)
            {
                // 屏蔽状态下按键进不去，发送期间临时放开，发完立刻关回去
                NativeMethods.EnableWindow(inputWindow, true);
                restored = true;
            }

            try
            {
                if (!WindowHosting.TryFocusInputWindow(OwnerWindow, inputWindow))
                {
                    throw new InvalidOperationException(
                        "无法把键盘焦点切到桌面分身，快捷键没发出去");
                }

                SendKeyStrokes(scanCodes, keyUps, extended);
            }
            finally
            {
                if (restored)
                {
                    NativeMethods.EnableWindow(inputWindow, false);
                }
            }
        }

        /// <summary>屏蔽/放开远程桌面侧的输入捕获窗口（跨空间拖动时用）。</summary>
        internal void SetInputEnabled(bool enabled)
        {
            bool wasBlocked = _inputBlocked;
            _inputBlocked = !enabled;
            _cursorGuard.Enabled = _inputBlocked;
            if (_inputBlocked && !wasBlocked)
            {
                // 进入观看模式时控件可能已经把鼠标抓在手里，还回去，光标就能收回本地
                NativeMethods.ReleaseCapture();
            }

            IntPtr inputWindow = FindInputCaptureWindow();
            if (inputWindow == IntPtr.Zero)
            {
                return;
            }

            NativeMethods.EnableWindow(inputWindow, enabled);
        }

        /// <summary>
        /// 观看模式的消息闸门。
        ///
        /// 只把 Input Capture Window 禁用并不够：鼠标移动是控件窗口自己转发给远程会话的，
        /// 不经过那扇窗，所以画面里的鼠标照样跟着动。这里在消息派发之前把指向远程桌面
        /// 控件的鼠标/键盘消息整条丢掉，观看模式才真的是「只能看」。
        /// </summary>
        public bool PreFilterMessage(ref Message m)
        {
            if (!_inputBlocked || !BelongsToRemoteDesktop(m.HWnd))
            {
                return false;
            }

            switch (m.Msg)
            {
                case WmInput:
                case WmKeyDown:
                case WmKeyUp:
                case WmChar:
                case WmSysKeyDown:
                case WmSysKeyUp:
                case WmSysChar:
                case WmMouseMove:
                case WmLeftButtonDown:
                case WmLeftButtonUp:
                case WmLeftButtonDoubleClick:
                case WmRightButtonDown:
                case WmRightButtonUp:
                case WmRightButtonDoubleClick:
                case WmMiddleButtonDown:
                case WmMiddleButtonUp:
                case WmMiddleButtonDoubleClick:
                case WmMouseWheel:
                case WmXButtonDown:
                case WmXButtonUp:
                case WmXButtonDoubleClick:
                case WmMouseHWheel:
                    return true;
                default:
                    return false;
            }
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                Application.RemoveMessageFilter(this);
                _cursorGuard.Dispose();
            }

            base.Dispose(disposing);
        }

        private bool BelongsToRemoteDesktop(IntPtr hwnd)
        {
            if (hwnd == IntPtr.Zero || !IsHandleCreated)
            {
                return false;
            }

            try
            {
                IntPtr root = Handle;
                return hwnd == root || NativeMethods.IsChild(root, hwnd);
            }
            catch (Exception)
            {
                // 控件正在销毁，句柄随时可能失效
                return false;
            }
        }

        private void SendKeyStrokes(int[] scanCodes, bool[] keyUps, bool[] extended)
        {
            int count = scanCodes.Length;
            var keyUpStates = new short[count];
            var keyData = new int[count];
            for (int i = 0; i < count; i++)
            {
                keyUpStates[i] = keyUps[i] ? VariantTrue : VariantFalse;
                keyData[i] = scanCodes[i] | (extended[i] ? 0x0100 : 0);
            }

            var nonScriptable = (IMsRdpClientNonScriptable)Ocx();
            // 与 BetterGI 一致：传第一个元素的引用，让 COM 直接读这块连续内存
            nonScriptable.SendKeys(count, ref keyUpStates[0], ref keyData[0]);
        }

        private IntPtr FindInputCaptureWindow()
        {
            if (_inputCaptureWindow != IntPtr.Zero && NativeMethods.IsWindow(_inputCaptureWindow))
            {
                return _inputCaptureWindow;
            }

            if (!IsHandleCreated)
            {
                return IntPtr.Zero;
            }

            _inputCaptureWindow = NativeMethods.FindInputCaptureWindow(Handle);
            return _inputCaptureWindow;
        }

        private object Ocx()
        {
            if (!IsHandleCreated)
            {
                // 触发句柄创建，AxHost 会在其中实例化 ActiveX 控件
                IntPtr handle = Handle;
            }

            object ocx = GetOcx();
            if (ocx == null)
            {
                throw new InvalidOperationException("远程桌面控件尚未初始化");
            }

            return ocx;
        }

        /// <summary>AxHost 的 DesktopWidth/Height 有效范围。</summary>
        private static int ClampDesktopDimension(int value)
        {
            if (value < 200)
            {
                return 200;
            }

            return value > 8192 ? 8192 : value;
        }

        /// <summary>
        /// 取最近的断开原因描述，取不到时返回空串。
        /// 没有订阅 IMsTscAxEvents 事件，所以只能主动读 ExtendedDisconnectReason。
        /// </summary>
        internal string DescribeDisconnectReason()
        {
            try
            {
                object ocx = Ocx();
                object extended = GetProperty(ocx, "ExtendedDisconnectReason");
                int extendedCode = extended == null ? 0 : Convert.ToInt32(extended);
                if (extendedCode == 0)
                {
                    return string.Empty;
                }

                object description = InvokeMethodWithArgs(ocx, "GetErrorDescription", 0, extendedCode);
                string text = description as string;
                if (!string.IsNullOrEmpty(text))
                {
                    return text;
                }

                return "连接中断（原因代码 " + extendedCode + "）";
            }
            catch (Exception)
            {
                return string.Empty;
            }
        }

        private static object GetProperty(object target, string name)
        {
            return target.GetType().InvokeMember(
                name,
                BindingFlags.GetProperty,
                null,
                target,
                null);
        }

        /// <summary>设置属性，属性不存在时忽略（不同 Windows 版本的接口不一样）。</summary>
        private static void TrySetProperty(object target, string name, object value)
        {
            try
            {
                SetProperty(target, name, value);
            }
            catch (Exception)
            {
                // 老系统上的远程桌面控件可能没有这个属性
            }
        }

        private static void SetProperty(object target, string name, object value)
        {
            target.GetType().InvokeMember(
                name,
                BindingFlags.SetProperty,
                null,
                target,
                new[] { value });
        }

        private static void InvokeMethod(object target, string name)
        {
            target.GetType().InvokeMember(
                name,
                BindingFlags.InvokeMethod,
                null,
                target,
                null);
        }

        private static object InvokeMethodWithArgs(object target, string name, params object[] args)
        {
            return target.GetType().InvokeMember(
                name,
                BindingFlags.InvokeMethod,
                null,
                target,
                args);
        }

        private static void SetExtendedProperty(IMsRdpExtendedSettings settings, string name, object value)
        {
            settings.set_Property(name, ref value);
        }
    }

    /// <summary>桌面分身窗口里可以发送的组合键。</summary>
    internal enum DesktopShortcut
    {
        ShowDesktop,
        TaskView
    }

    /// <summary>IMsRdpClientAdvancedSettings7 之外，子会话连接需要的扩展设置接口。</summary>
    [ComImport]
    [Guid("302D8188-0052-4807-806A-362B628F9AC5")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IMsRdpExtendedSettings
    {
        void set_Property([MarshalAs(UnmanagedType.BStr)] string propertyName, ref object propertyValue);

        void get_Property([MarshalAs(UnmanagedType.BStr)] string propertyName, ref object propertyValue);
    }

    /// <summary>
    /// IMsRdpClientNonScriptable 仅用于 SendKeys。
    /// 成员顺序必须与 COM 接口定义完全一致，否则调用会崩，因此保持 BetterGI 中的原始顺序。
    /// </summary>
    [ComImport]
    [Guid("2F079C4C-87B2-4AFD-97AB-20CDB43038AE")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IMsRdpClientNonScriptable
    {
        void put_ClearTextPassword([MarshalAs(UnmanagedType.BStr)] string value);

        void put_PortablePassword([MarshalAs(UnmanagedType.BStr)] string value);

        [return: MarshalAs(UnmanagedType.BStr)]
        string get_PortablePassword();

        void put_PortableSalt([MarshalAs(UnmanagedType.BStr)] string value);

        [return: MarshalAs(UnmanagedType.BStr)]
        string get_PortableSalt();

        void put_BinaryPassword([MarshalAs(UnmanagedType.BStr)] string value);

        [return: MarshalAs(UnmanagedType.BStr)]
        string get_BinaryPassword();

        void put_BinarySalt([MarshalAs(UnmanagedType.BStr)] string value);

        [return: MarshalAs(UnmanagedType.BStr)]
        string get_BinarySalt();

        void ResetPassword();

        void NotifyRedirectDeviceChange(uint wParam, IntPtr lParam);

        void SendKeys(int numKeys, [In] ref short keyUpStates, [In] ref int keyData);
    }
}