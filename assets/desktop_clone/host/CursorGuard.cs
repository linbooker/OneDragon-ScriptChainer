using System;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Windows.Forms;

namespace OneDragonScriptChainer.DesktopClone
{
    /// <summary>
    /// 观看模式下的本地光标护栏。
    ///
    /// 远程会话里那个光标并不是只在画面里动：远程程序把它挪走时，远程桌面控件会顺手
    /// 把本地鼠标也搬过去；控件抓到鼠标时，光标还会被夹在画面区域里出不来。观看模式
    /// 只该「看」，所以这里装一个低级鼠标钩子记下真实鼠标的位置，再定时把被搬走的光标
    /// 放回去、把捕获和裁剪解开。
    /// </summary>
    internal sealed class CursorGuard : IDisposable
    {
        /// <summary>护栏的检查间隔：够快，人眼看不出光标跳了一下。</summary>
        private const int TickMilliseconds = 50;

        private readonly Timer _timer;

        /// <summary>
        /// 钩子回调的委托必须留一份引用，否则被回收之后系统再回调就会崩。
        /// </summary>
        private readonly NativeMethods.LowLevelMouseProc _hookProc;

        private readonly IntPtr _hook;

        private int _restoreX;
        private int _restoreY;

        /// <summary>远程桌面控件的窗口句柄：只收拾它抓走的捕获，别去动别的程序的。</summary>
        private IntPtr _remoteHandle;

        private bool _disposed;

        internal CursorGuard()
        {
            _hookProc = OnMouseEvent;
            _hook = NativeMethods.SetWindowsHookEx(
                NativeMethods.WhMouseLl,
                _hookProc,
                NativeMethods.GetModuleHandleW(null),
                0);

            _timer = new Timer();
            _timer.Interval = TickMilliseconds;
            _timer.Tick += OnTimerTick;

            SyncToCursor();
        }

        /// <summary>是否正在护栏（观看模式）。</summary>
        internal bool Enabled
        {
            get { return _timer.Enabled; }
            set
            {
                if (_disposed)
                {
                    return;
                }

                if (value)
                {
                    SyncToCursor();
                    _timer.Start();
                }
                else
                {
                    _timer.Stop();
                }
            }
        }

        public void Dispose()
        {
            if (_disposed)
            {
                return;
            }

            _disposed = true;
            _timer.Stop();
            _timer.Tick -= OnTimerTick;
            _timer.Dispose();

            if (_hook != IntPtr.Zero)
            {
                NativeMethods.UnhookWindowsHookEx(_hook);
            }
        }

        /// <summary>
        /// 告诉护栏远程桌面控件是哪个窗口。只有它的捕获和裁剪才会被解开——主界面自己拖
        /// 小窗、拉滚动条也要用捕获，不能一视同仁地抢。
        /// </summary>
        internal void AttachTo(IntPtr remoteHandle)
        {
            _remoteHandle = remoteHandle;
        }

        private bool BelongsToRemote(IntPtr hwnd)
        {
            if (hwnd == IntPtr.Zero || _remoteHandle == IntPtr.Zero)
            {
                return false;
            }

            if (hwnd == _remoteHandle)
            {
                return true;
            }

            try
            {
                return NativeMethods.IsChild(_remoteHandle, hwnd);
            }
            catch (Exception)
            {
                return false;
            }
        }

        private static bool IsWholeDesktop(NativeMethods.Rect clip)
        {
            Rectangle screen = SystemInformation.VirtualScreen;
            return clip.Left <= screen.Left
                && clip.Top <= screen.Top
                && clip.Right >= screen.Right
                && clip.Bottom >= screen.Bottom;
        }

        private void SyncToCursor()
        {
            // 必须用物理坐标：低级钩子给的是物理像素，GetCursorPos 在未做 DPI 感知的进程里
            // 会被虚拟化（4K 200% 屏上两者差一倍），拿两套坐标互相比对会直接把光标钉到屏幕角落。
            NativeMethods.Point current;
            if (NativeMethods.GetPhysicalCursorPos(out current))
            {
                _restoreX = current.X;
                _restoreY = current.Y;
            }
        }

        /// <summary>
        /// 低级鼠标钩子：只做一件事——记下真实鼠标最后一次被物理移动到的位置。
        /// 远程会话搬动光标不会产生这个回调，所以两者的差就是「被搬走的量」。
        /// </summary>
        private IntPtr OnMouseEvent(int code, IntPtr wParam, IntPtr lParam)
        {
            if (code >= 0 && wParam == (IntPtr)NativeMethods.WmMouseMove)
            {
                var data = (NativeMethods.MouseHookStruct)Marshal.PtrToStructure(
                    lParam,
                    typeof(NativeMethods.MouseHookStruct));
                _restoreX = data.Point.X;
                _restoreY = data.Point.Y;
            }

            return NativeMethods.CallNextHookEx(_hook, code, wParam, lParam);
        }

        private void OnTimerTick(object sender, EventArgs e)
        {
            // 控件抓着鼠标时光标会被夹在画面里，先还回去（只管远程控件抓的那一下）
            if (BelongsToRemote(NativeMethods.GetCapture()))
            {
                NativeMethods.ReleaseCapture();
            }

            // 裁剪范围被改小时，指针就走不出那扇窗了。只有光标正卡在远程画面里才动裁剪，
            // 免得把别的程序设的范围一并抹掉。
            NativeMethods.Rect clip;
            if (NativeMethods.GetClipCursor(out clip) && !IsWholeDesktop(clip) && CursorInsideRemote())
            {
                NativeMethods.ClipCursor(IntPtr.Zero);
            }

            // 光标被远程会话搬到别处时，放回真实鼠标的位置（同样只用物理坐标，见 SyncToCursor）
            NativeMethods.Point current;
            if (NativeMethods.GetPhysicalCursorPos(out current)
                && (current.X != _restoreX || current.Y != _restoreY))
            {
                NativeMethods.SetPhysicalCursorPos(_restoreX, _restoreY);
            }
        }

        private bool CursorInsideRemote()
        {
            if (_remoteHandle == IntPtr.Zero)
            {
                return false;
            }

            NativeMethods.Rect bounds;
            if (!NativeMethods.GetWindowRect(_remoteHandle, out bounds))
            {
                return false;
            }

            NativeMethods.Point current;
            if (!NativeMethods.GetCursorPos(out current))
            {
                return false;
            }

            // 这里两边都用「虚拟化」坐标：GetWindowRect 在未做 DPI 感知的进程里也是虚拟化的，
            // 和 GetCursorPos 同一套坐标系，可以直接比
            return current.X >= bounds.Left && current.X < bounds.Right
                && current.Y >= bounds.Top && current.Y < bounds.Bottom;
        }
    }
}
