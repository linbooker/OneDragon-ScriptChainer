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

        /// <summary>
        /// 钩子超过这么久没有回调，就认为它已经被系统卸掉了。
        ///
        /// 系统对低级钩子有 LowLevelHooksTimeout（默认 300ms）：钩子所属的线程没及时处理，
        /// 系统会**悄悄**把钩子摘掉，而且不通知谁。UI 线程卡在注销、等子会话关闭这类地方
        /// 几秒钟就足够触发一次。
        /// </summary>
        private const int HookAliveMilliseconds = 800;

        /// <summary>发现钩子没了之后，隔这么久才重新装一次，别每 50ms 折腾一遍。</summary>
        private const int HookRepairMilliseconds = 2000;

        private readonly Timer _timer;

        /// <summary>
        /// 钩子回调的委托必须留一份引用，否则被回收之后系统再回调就会崩。
        /// </summary>
        private readonly NativeMethods.LowLevelMouseProc _hookProc;

        /// <summary>不 readonly：钩子被系统摘掉之后要重装。</summary>
        private IntPtr _hook;

        /// <summary>最近一次钩子回调的时刻（Environment.TickCount），用来判断钩子还活着没有。</summary>
        private int _lastHookEventTicks;

        /// <summary>最近一次重装钩子的时刻。</summary>
        private int _lastHookRepairTicks;

        private int _restoreX;
        private int _restoreY;

        /// <summary>远程桌面控件的窗口句柄：只收拾它抓走的捕获，别去动别的程序的。</summary>
        private IntPtr _remoteHandle;

        private bool _disposed;

        internal CursorGuard()
        {
            _hookProc = OnMouseEvent;
            EnsureHook();

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
                if (_disposed || value == _timer.Enabled)
                {
                    // 状态没变就别反复装卸钩子：状态轮询每 500ms 会调一次
                    return;
                }

                if (value)
                {
                    EnsureHook();
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

        /// <summary>重新装一次低级鼠标钩子。装不上就返回零柄，护栏退化成只管捕获和裁剪。</summary>
        private void EnsureHook()
        {
            if (_hook != IntPtr.Zero)
            {
                NativeMethods.UnhookWindowsHookEx(_hook);
                _hook = IntPtr.Zero;
            }

            _hook = NativeMethods.SetWindowsHookEx(
                NativeMethods.WhMouseLl,
                _hookProc,
                NativeMethods.GetModuleHandleW(null),
                0);
            _lastHookRepairTicks = Environment.TickCount;
        }

        /// <summary>钩子最近还在回调吗？系统摘钩子不会通知我们，只能这样自己看。</summary>
        private bool HookAlive()
        {
            return unchecked(Environment.TickCount - _lastHookEventTicks) <= HookAliveMilliseconds;
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
            _lastHookEventTicks = Environment.TickCount;
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

            // 钩子被系统摘掉之后「真实鼠标位置」就再也不会更新了。这时候还硬把光标搬回那个
            // 旧位置，等于把鼠标钉死在一动不动的点上 —— 注销、等子会话关闭之类会卡住 UI 线程
            // 几秒的操作刚好能让钩子超时被摘，所以必须先确认钩子还活着。
            if (!ShouldRestoreCursor())
            {
                if (unchecked(Environment.TickCount - _lastHookRepairTicks) >= HookRepairMilliseconds)
                {
                    EnsureHook();
                }

                return;
            }

            // 光标被远程会话搬到别处时，放回真实鼠标的位置（同样只用物理坐标，见 SyncToCursor）
            NativeMethods.Point current;
            if (NativeMethods.GetPhysicalCursorPos(out current)
                && (current.X != _restoreX || current.Y != _restoreY))
            {
                NativeMethods.SetPhysicalCursorPos(_restoreX, _restoreY);
            }
        }

        /// <summary>
        /// 现在能不能把光标搬回「真实鼠标位置」。
        ///
        /// 只有钩子最近还在回调时才敢搬：钩子一旦被系统摘掉，记下来的位置就再也不更新，
        /// 拿它去覆盖光标等于把鼠标钉死。这是「注销分身后鼠标动不了」的根因。
        /// </summary>
        private bool ShouldRestoreCursor()
        {
            return !_disposed && HookAlive();
        }

        /// <summary>
        /// 断开或注销之后把光标从远程桌面手里彻底收回来。
        ///
        /// 控件连着会话时会把光标裁剪在自己的画面范围内。断开之后这条裁剪不一定被解开，
        /// 而画面窗口又会被挪到 (-32000, -32000) 这种看不见的地方、缩成 1x1，裁剪范围跟着
        /// 变成屏幕外的一个点 —— 本地鼠标就推不动了，用户看到的正是「注销分身后鼠标不动」。
        /// 所以这里解开捕获、清掉裁剪；光标已经被钉在屏幕外的话，再搬回可见区域。
        /// </summary>
        internal void FreeCursor()
        {
            if (_disposed)
            {
                return;
            }

            IntPtr capture = NativeMethods.GetCapture();
            if (capture != IntPtr.Zero && (_remoteHandle == IntPtr.Zero || BelongsToRemote(capture)))
            {
                NativeMethods.ReleaseCapture();
            }

            NativeMethods.ClipCursor(IntPtr.Zero);

            NativeMethods.Point current;
            if (NativeMethods.GetCursorPos(out current) && !IsOnScreen(current))
            {
                Rectangle work = Screen.PrimaryScreen.WorkingArea;
                NativeMethods.SetCursorPos(work.Left + (work.Width / 2), work.Top + (work.Height / 2));
            }

            SyncToCursor();
        }

        /// <summary>
        /// 光标是否还落在看得见的那块桌面上。这里两边都用「虚拟化」坐标：GetCursorPos 与
        /// SystemInformation.VirtualScreen 在没做 DPI 感知的进程里是同一套，可以直接比。
        /// </summary>
        private static bool IsOnScreen(NativeMethods.Point point)
        {
            Rectangle screen = SystemInformation.VirtualScreen;
            return point.X >= screen.Left && point.X < screen.Right
                && point.Y >= screen.Top && point.Y < screen.Bottom;
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

