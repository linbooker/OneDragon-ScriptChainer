using System;
using System.Runtime.InteropServices;
using System.Text;

namespace OneDragonScriptChainer.DesktopClone
{
    /// <summary>
    /// 桌面分身需要的 Win32 / WTS API。
    /// 全部逻辑与 BetterGenshinImpact 的 ChildSessionNativeMethods 保持一致，只是按本项目的用法裁剪。
    /// </summary>
    internal static class NativeMethods
    {
        internal const int ErrorNotFound = 1168;
        internal const uint NoSessionId = 0xFFFFFFFF;

        internal const int GwlStyle = -16;
        internal const int GwlHwndParent = -8;
        internal const int WtsWinStationName = 6;

        internal const uint SwpNoSize = 0x0001;
        internal const uint SwpNoMove = 0x0002;
        internal const uint SwpNoZOrder = 0x0004;
        internal const uint SwpNoActivate = 0x0010;
        internal const int SwHide = 0;
        internal const int SwShowNoActivate = 4;
        internal static readonly IntPtr HwndTopMost = new IntPtr(-1);
        internal static readonly IntPtr HwndNoTopMost = new IntPtr(-2);
        internal static readonly IntPtr HwndTop = IntPtr.Zero;

        internal const int WsChild = 0x40000000;
        internal const int WsPopup = unchecked((int)0x80000000);
        internal const int WsCaption = 0x00C00000;
        internal const int WsThickFrame = 0x00040000;

        internal const string InputCaptureWindowTitle = "Input Capture Window";

        [DllImport("wtsapi32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        internal static extern bool WTSEnableChildSessions([MarshalAs(UnmanagedType.Bool)] bool enabled);

        [DllImport("wtsapi32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        internal static extern bool WTSIsChildSessionsEnabled(out int enabled);

        [DllImport("wtsapi32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        internal static extern bool WTSGetChildSessionId(out uint sessionId);

        [DllImport("wtsapi32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        internal static extern bool WTSLogoffSession(IntPtr server, uint sessionId, [MarshalAs(UnmanagedType.Bool)] bool wait);

        [DllImport("wtsapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        [return: MarshalAs(UnmanagedType.Bool)]
        internal static extern bool WTSQuerySessionInformationW(
            IntPtr server,
            uint sessionId,
            int informationClass,
            out IntPtr buffer,
            out int bytesReturned);

        [DllImport("wtsapi32.dll")]
        internal static extern void WTSFreeMemory(IntPtr memory);

        [DllImport("user32.dll", SetLastError = true)]
        internal static extern IntPtr SetParent(IntPtr child, IntPtr parent);

        [DllImport("user32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        internal static extern bool SetWindowPos(
            IntPtr hwnd,
            IntPtr insertAfter,
            int x,
            int y,
            int width,
            int height,
            uint flags);

        [DllImport("user32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        internal static extern bool ShowWindow(IntPtr hwnd, int command);

        [DllImport("user32.dll", SetLastError = true)]
        internal static extern IntPtr GetParent(IntPtr hwnd);

        [DllImport("user32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        internal static extern bool IsWindowVisible(IntPtr hwnd);

        [DllImport("user32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        internal static extern bool GetClientRect(IntPtr hwnd, out Rect rect);

        [DllImport("user32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        internal static extern bool MoveWindow(IntPtr hwnd, int x, int y, int width, int height, [MarshalAs(UnmanagedType.Bool)] bool repaint);

        [DllImport("user32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        internal static extern bool EnableWindow(IntPtr hwnd, [MarshalAs(UnmanagedType.Bool)] bool enabled);

        /// <summary>hwnd 是否是 parent 的后代窗口（不含 parent 自己）。</summary>
        [DllImport("user32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        internal static extern bool IsChild(IntPtr parent, IntPtr hwnd);

        /// <summary>释放本线程当前持有的鼠标捕获；没有捕获时什么也不做。</summary>
        [DllImport("user32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        internal static extern bool ReleaseCapture();

        [DllImport("user32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        internal static extern bool IsWindow(IntPtr hwnd);

        [DllImport("user32.dll", SetLastError = true)]
        internal static extern int GetWindowLong(IntPtr hwnd, int index);

        [DllImport("user32.dll", SetLastError = true)]
        internal static extern int SetWindowLong(IntPtr hwnd, int index, int newStyle);

        [DllImport("user32.dll", SetLastError = true, EntryPoint = "SetWindowLongPtrW")]
        internal static extern IntPtr SetWindowLongPtrW(IntPtr hwnd, int index, IntPtr value);

        [DllImport("user32.dll", SetLastError = true, EntryPoint = "GetWindowLongPtrW")]
        internal static extern IntPtr GetWindowLongPtrW(IntPtr hwnd, int index);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        internal static extern int GetWindowTextW(IntPtr hwnd, StringBuilder text, int maxCount);

        internal delegate bool EnumChildProc(IntPtr hwnd, IntPtr lParam);

        [DllImport("user32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        internal static extern bool EnumChildWindows(IntPtr parent, EnumChildProc callback, IntPtr lParam);

        /// <summary>标准输入句柄编号。stdin = -10。</summary>
        internal const int StandardInputHandle = -10;

        internal static readonly IntPtr InvalidHandleValue = new IntPtr(-1);

        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern IntPtr GetStdHandle(int handleType);

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        internal static extern bool ReadFile(
            IntPtr file,
            byte[] buffer,
            int numberOfBytesToRead,
            out int numberOfBytesRead,
            IntPtr overlapped);

        [DllImport("user32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        internal static extern bool GetWindowRect(IntPtr hwnd, out Rect rect);

        [DllImport("user32.dll")]
        internal static extern int GetSystemMetrics(int index);

        internal const int SmCxScreen = 0;
        internal const int SmCyScreen = 1;

        /// <summary>把窗口的物理像素矩形拼成一行文本，用于排查尺寸/DPI 问题。</summary>
        [DllImport("user32.dll")]
        private static extern bool GetClientRect(IntPtr hwnd, out NativeRect rect);

        [StructLayout(LayoutKind.Sequential)]
        private struct NativeRect
        {
            internal int Left;
            internal int Top;
            internal int Right;
            internal int Bottom;
        }

        internal static string DescribeClientRect(IntPtr hwnd)
        {
            if (hwnd == IntPtr.Zero)
            {
                return "0x0";
            }

            NativeRect rect;
            if (!GetClientRect(hwnd, out rect))
            {
                return "0x0";
            }

            return (rect.Right - rect.Left).ToString()
                + "x" + (rect.Bottom - rect.Top).ToString();
        }

        internal static string DescribeStyle(IntPtr hwnd)
        {
            if (hwnd == IntPtr.Zero)
            {
                return "0";
            }

            int style = (int)GetWindowLongPtrW(hwnd, -16).ToInt64();
            return "0x" + style.ToString("X8");
        }

        internal static string DescribeWindowRect(IntPtr hwnd)
        {
            Rect rect;
            if (hwnd == IntPtr.Zero || !GetWindowRect(hwnd, out rect))
            {
                return "unknown";
            }

            return string.Format(
                "{0},{1} {2}x{3} 屏幕={4}x{5}",
                rect.Left,
                rect.Top,
                rect.Width,
                rect.Height,
                GetSystemMetrics(SmCxScreen),
                GetSystemMetrics(SmCyScreen));
        }

        [DllImport("user32.dll", SetLastError = true)]
        internal static extern bool SetProcessDpiAwarenessContext(IntPtr value);

        [DllImport("shcore.dll", SetLastError = true)]
        internal static extern int SetProcessDpiAwareness(int value);

        [DllImport("user32.dll", SetLastError = true)]
        internal static extern bool SetProcessDPIAware();

        internal static readonly IntPtr PerMonitorAwareV2 = new IntPtr(-4);

        /// <summary>声明本进程按监视器 DPI 感知，坐标即真实像素。</summary>
        internal static void EnablePerMonitorDpiAwareness()
        {
            try
            {
                if (SetProcessDpiAwarenessContext(PerMonitorAwareV2))
                {
                    return;
                }
            }
            catch (Exception)
            {
                // 老系统没有这个导出，继续往下试
            }

            try
            {
                if (SetProcessDpiAwareness(2) == 0)
                {
                    return;
                }
            }
            catch (Exception)
            {
            }

            try
            {
                SetProcessDPIAware();
            }
            catch (Exception)
            {
            }
        }

        [StructLayout(LayoutKind.Sequential)]
        internal struct Rect
        {
            public int Left;
            public int Top;
            public int Right;
            public int Bottom;

            public int Width
            {
                get { return Right - Left; }
            }

            public int Height
            {
                get { return Bottom - Top; }
            }
        }

        /// <summary>读取子会话 id；不存在（ERROR_NOT_FOUND 或其它失败）时返回 0。</summary>
        internal static uint TryGetChildSessionId()
        {
            uint sessionId;
            if (WTSGetChildSessionId(out sessionId))
            {
                return sessionId == NoSessionId ? 0u : sessionId;
            }

            return 0u;
        }

        /// <summary>查询会话的 WinStation 名称（例如 RDP-Tcp#0），失败返回空串。</summary>
        internal static string QueryWinStationName(uint sessionId)
        {
            IntPtr buffer;
            int bytes;
            if (!WTSQuerySessionInformationW(IntPtr.Zero, sessionId, WtsWinStationName, out buffer, out bytes)
                || buffer == IntPtr.Zero)
            {
                return string.Empty;
            }

            try
            {
                return Marshal.PtrToStringUni(buffer) ?? string.Empty;
            }
            finally
            {
                WTSFreeMemory(buffer);
            }
        }

        /// <summary>
        /// 把窗口的「所有者」设成主窗口，这样它会永远压在主窗口上面，
        /// 主窗口最小化时它也跟着消失，但两边仍然是各自独立的顶层窗口。
        /// </summary>
        internal static void SetOwner(IntPtr hwnd, IntPtr owner)
        {
            if (hwnd == IntPtr.Zero || owner == IntPtr.Zero)
            {
                return;
            }

            SetWindowLongPtr(hwnd, GwlHwndParent, owner);
        }

        /// <summary>64 位下才是 SetWindowLongPtr，32 位进程回落到 SetWindowLong。</summary>
        internal static IntPtr SetWindowLongPtr(IntPtr hwnd, int index, IntPtr value)
        {
            if (IntPtr.Size == 8)
            {
                return SetWindowLongPtrW(hwnd, index, value);
            }

            return new IntPtr(SetWindowLong(hwnd, index, value.ToInt32()));
        }

        [DllImport("user32.dll", SetLastError = true)]
        internal static extern IntPtr GetWindow(IntPtr hwnd, int command);

        internal const int GwOwner = 4;
        internal const int GwlExStyle = -20;
        internal const int WsExNoActivate = 0x08000000;
        internal const int WsExToolWindow = 0x00000080;

        /// <summary>窗口当前是否置顶（WS_EX_TOPMOST）。</summary>
        internal static bool IsTopMost(IntPtr hwnd)
        {
            if (hwnd == IntPtr.Zero)
            {
                return false;
            }

            IntPtr style;
            if (IntPtr.Size == 8)
            {
                style = GetWindowLongPtrW(hwnd, GwlExStyle);
            }
            else
            {
                style = new IntPtr(GetWindowLong(hwnd, GwlExStyle));
            }

            return (style.ToInt64() & 0x8L) == 0x8L;
        }

        /// <summary>窗口当前的拥有者句柄（0 表示没有）。</summary>
        internal static IntPtr GetOwner(IntPtr hwnd)
        {
            if (hwnd == IntPtr.Zero)
            {
                return IntPtr.Zero;
            }

            return GetWindow(hwnd, GwOwner);
        }

        /// <summary>把窗口提到最上层但不拿焦点，用来保证画面始终圧在主界面上面。</summary>
        internal static void RaiseNoActivate(IntPtr hwnd)
        {
            if (hwnd == IntPtr.Zero)
            {
                return;
            }

            SetWindowPos(hwnd, HwndTop, 0, 0, 0, 0, SwpNoMove | SwpNoSize | SwpNoActivate);
        }

        /// <summary>显示窗口、提到最上层，但不抢焦点。</summary>
        internal static void ShowNoActivate(IntPtr hwnd)
        {
            if (hwnd == IntPtr.Zero)
            {
                return;
            }

            // 只 ShowWindow 不会改变 Z 序，画面可能被主窗口或别的程序盖住
            SetWindowPos(hwnd, HwndTop, 0, 0, 0, 0, SwpNoMove | SwpNoSize | SwpNoActivate);
            ShowWindow(hwnd, SwShowNoActivate);
        }

        /// <summary>SW_SHOW</summary>
        internal const int SwShow = 5;

        /// <summary>隐藏窗口。</summary>
        internal static void HideWindow(IntPtr hwnd)
        {
            if (hwnd == IntPtr.Zero)
            {
                return;
            }

            ShowWindow(hwnd, SwHide);
        }

        /// <summary>置顶/取消置顶。</summary>
        internal static bool SetTopMost(IntPtr hwnd, bool topMost)
        {
            if (hwnd == IntPtr.Zero)
            {
                return false;
            }

            return SetWindowPos(hwnd, topMost ? HwndTopMost : HwndNoTopMost, 0, 0, 0, 0,
                SwpNoMove | SwpNoSize | SwpNoActivate);
        }

        /// <summary>在远程桌面控件内部查找负责捕获键盘鼠标输入的窗口。</summary>
        internal static IntPtr FindInputCaptureWindow(IntPtr root)
        {
            if (root == IntPtr.Zero)
            {
                return IntPtr.Zero;
            }

            IntPtr found = IntPtr.Zero;
            EnumChildWindows(root, delegate(IntPtr hwnd, IntPtr lParam)
            {
                var builder = new StringBuilder(256);
                int length = GetWindowTextW(hwnd, builder, builder.Capacity);
                if (length > 0 && builder.ToString() == InputCaptureWindowTitle)
                {
                    found = hwnd;
                    return false;
                }

                return true;
            }, IntPtr.Zero);

            return found;
        }
    }
}