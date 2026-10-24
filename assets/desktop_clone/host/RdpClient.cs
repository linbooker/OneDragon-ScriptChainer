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
    internal sealed class RdpClient : AxHost
    {
        /// <summary>MsRdpClient10 NotSafeForScripting 的 CLSID，Windows 10 及以后可用。</summary>
        private const string RdpClientClassId = "A0C63C30-F08D-4AB4-907C-34905D770C7D";

        private const short VariantTrue = -1;
        private const short VariantFalse = 0;

        private const int KeyboardHookModeLocal = 0;
        private const int KeyboardHookModeRemote = 1;
        private const int AudioRedirectionModeLocal = 0;
        private const int AudioRedirectionModeRemote = 2;

        private IntPtr _inputCaptureWindow;

        private bool _inputBlocked;

        /// <summary>宿主窗口（切前台、把焦点拉进分身时用）。</summary>
        internal IntPtr OwnerWindow { get; set; }

        internal RdpClient()
            : base(RdpClientClassId)
        {
            Dock = DockStyle.Fill;
            BackColor = System.Drawing.Color.Black;
            TabStop = false;
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
        internal void ConnectToChildSession(
            int width,
            int height,
            bool smartSizing,
            bool sendSystemShortcuts,
            bool audioMuted,
            int rdpPort)
        {
            object ocx = Ocx();

            SetProperty(ocx, "Server", "localhost");
            SetProperty(ocx, "DesktopWidth", ClampDesktopDimension(width));
            SetProperty(ocx, "DesktopHeight", ClampDesktopDimension(height));
            SetProperty(ocx, "ColorDepth", 32);
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
            _inputBlocked = !enabled;
            IntPtr inputWindow = FindInputCaptureWindow();
            if (inputWindow == IntPtr.Zero)
            {
                return;
            }

            NativeMethods.EnableWindow(inputWindow, enabled);
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