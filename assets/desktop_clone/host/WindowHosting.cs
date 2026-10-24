using System;
using System.Runtime.InteropServices;

namespace OneDragonScriptChainer.DesktopClone
{
    /// <summary>
    /// 把宿主窗口「嵌进」界面窗口所需要的 Win32 调用。
    ///
    /// 界面进程（PySide6）给出自己某个控件（这里是页面里的画面控件）的 HWND，
    /// 这里把宿主窗口改成它的子窗口：窗口样式换成 WS_CHILD、位置相对父窗口客户区，
    /// 于是画面就成了界面里的一块，跟着页面滚动/隐藏/最小化，不再是一个独立窗口。
    /// </summary>
    internal static class WindowHosting
    {
        private const int GwlStyle = -16;

        private const int WsChild = 0x40000000;
        private const int WsPopup = unchecked((int)0x80000000);
        private const int WsCaption = 0x00C00000;
        private const int WsThickFrame = 0x00040000;
        private const int WsSysMenu = 0x00080000;
        private const int WsMinimizeBox = 0x00020000;
        private const int WsMaximizeBox = 0x00010000;
        private const int WsVisible = 0x10000000;

        private const uint SwpNoZOrder = 0x0004;
        private const uint SwpNoActivate = 0x0010;
        private const uint SwpFrameChanged = 0x0020;
        private const uint SwpShowWindow = 0x0040;

        [DllImport("user32.dll", SetLastError = true)]
        private static extern IntPtr SetParent(IntPtr child, IntPtr parent);

        [DllImport("user32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool SetWindowPos(
            IntPtr hwnd,
            IntPtr insertAfter,
            int x,
            int y,
            int width,
            int height,
            uint flags);

        [DllImport("user32.dll", SetLastError = true)]
        private static extern int GetWindowLong(IntPtr hwnd, int index);

        [DllImport("user32.dll", SetLastError = true)]
        private static extern int SetWindowLong(IntPtr hwnd, int index, int newLong);

        [DllImport("user32.dll", SetLastError = true)]
        private static extern IntPtr SetFocus(IntPtr hwnd);

        [DllImport("user32.dll", SetLastError = true)]
        private static extern IntPtr GetFocus();

        [DllImport("user32.dll")]
        private static extern IntPtr GetForegroundWindow();

        [DllImport("user32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool SetForegroundWindow(IntPtr hwnd);

        [DllImport("user32.dll")]
        private static extern IntPtr GetAncestor(IntPtr hwnd, int flags);

        private const int GaRoot = 2;

        /// <summary>取窗口所在顶层窗口（用来把本程序切到前台）。</summary>
        internal static IntPtr RootWindow(IntPtr hwnd)
        {
            if (hwnd == IntPtr.Zero)
            {
                return IntPtr.Zero;
            }

            IntPtr root = GetAncestor(hwnd, GaRoot);
            return root == IntPtr.Zero ? hwnd : root;
        }

        [DllImport("user32.dll", SetLastError = true)]
        private static extern uint GetWindowThreadProcessId(IntPtr hwnd, IntPtr processId);

        [DllImport("kernel32.dll")]
        private static extern uint GetCurrentThreadId();

        [DllImport("user32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, [MarshalAs(UnmanagedType.Bool)] bool attach);

        /// <summary>
        /// 把窗口挂成 parent 的子窗口，并摆到 (0, 0, width, height)。
        /// 必须在窗口句柄已经创建、且窗口不是 TopLevel 之后调用。
        /// </summary>
        internal static bool Embed(IntPtr hwnd, IntPtr parent, int width, int height)
        {
            if (hwnd == IntPtr.Zero || parent == IntPtr.Zero)
            {
                return false;
            }

            // 先改样式再 SetParent：带 WS_POPUP 的窗口变成子窗口时，
            // 系统会保留弹出窗口的边框/阴影，画面因此偏移几个像素
            int style = GetWindowLong(hwnd, GwlStyle);
            style &= ~(WsPopup | WsCaption | WsThickFrame | WsSysMenu | WsMinimizeBox | WsMaximizeBox);
            style |= WsChild | WsVisible;
            SetWindowLong(hwnd, GwlStyle, style);

            SetParent(hwnd, parent);

            return SetWindowPos(
                hwnd,
                IntPtr.Zero,
                0,
                0,
                Math.Max(1, width),
                Math.Max(1, height),
                SwpFrameChanged | SwpNoZOrder | SwpNoActivate | SwpShowWindow);
        }

        /// <summary>调整已经嵌好的窗口尺寸（子窗口坐标相对父窗口客户区）。</summary>
        internal static bool SetChildBounds(IntPtr hwnd, int x, int y, int width, int height)
        {
            if (hwnd == IntPtr.Zero)
            {
                return false;
            }

            return SetWindowPos(
                hwnd,
                IntPtr.Zero,
                x,
                y,
                Math.Max(1, width),
                Math.Max(1, height),
                SwpFrameChanged | SwpNoZOrder | SwpNoActivate | SwpShowWindow);
        }

        /// <summary>
        /// 把键盘焦点切到远程桌面的输入捕获窗口。
        ///
        /// 宿主是独立进程，跨进程 SetFocus 前必须先把本线程挂到前台线程的输入队列上，
        /// 否则 SetFocus 会静默失败——这正是「发 Win+D 没反应」的原因。
        /// </summary>
        internal static bool TryFocusInputWindow(IntPtr ownerWindow, IntPtr inputWindow)
        {
            if (inputWindow == IntPtr.Zero)
            {
                return false;
            }

            if (GetFocus() == inputWindow)
            {
                return true;
            }

            IntPtr foreground = GetForegroundWindow();
            if (ownerWindow != IntPtr.Zero && foreground != ownerWindow)
            {
                // 让本程序回到前台，否则前台线程不会把键盘交给我们的子窗口
                SetForegroundWindow(ownerWindow);
                foreground = GetForegroundWindow();
            }

            uint currentThread = GetCurrentThreadId();
            uint foregroundThread = foreground == IntPtr.Zero ? currentThread : GetWindowThreadProcessId(foreground, IntPtr.Zero);
            bool attached = foregroundThread != 0
                && foregroundThread != currentThread
                && AttachThreadInput(currentThread, foregroundThread, true);

            try
            {
                SetFocus(inputWindow);
            }
            finally
            {
                if (attached)
                {
                    AttachThreadInput(currentThread, foregroundThread, false);
                }
            }

            return GetFocus() == inputWindow;
        }

        /// <summary>窗口当前是否已经是 parent 的子窗口。</summary>
        internal static bool IsChildOf(IntPtr hwnd, IntPtr parent)
        {
            return NativeMethods.GetParent(hwnd) == parent;
        }
    }
}