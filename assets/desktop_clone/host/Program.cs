using System;
using System.Globalization;
using System.Windows.Forms;

namespace OneDragonScriptChainer.DesktopClone
{
    /// <summary>
    /// 桌面分身宿主进程入口。
    ///
    /// 由 Python 端（PySide6）以子进程方式启动：
    ///   DesktopCloneHost.exe --parent 1234567
    /// 其中 1234567 是界面中「画面」控件所在主窗口的句柄，仅作为窗口 owner 使用，
    /// 目的是让画面窗口始终压在程序主窗口上面（不是跨进程 SetParent 当子窗口）。
    /// 之后所有交互都走标准输入输出上的单行协议（见 ProtocolWriter）。
    /// </summary>
    internal static class Program
    {
        [STAThread]
        private static int Main(string[] args)
        {
            IntPtr parentHandle = IntPtr.Zero;
            for (int i = 0; i < args.Length - 1; i++)
            {
                if (!string.Equals(args[i], "--parent", StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                long value;
                if (long.TryParse(args[i + 1], NumberStyles.Integer, CultureInfo.InvariantCulture, out value))
                {
                    parentHandle = new IntPtr(value);
                }
            }

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            // 必须赶在创建任何窗口之前声明 DPI 感知：否则窗口会被系统按缩放比例
            // 放大，位置和尺寸都跟界面端算出来的对不上（画面偏到右下方）
            NativeMethods.EnablePerMonitorDpiAwareness();
            Application.Run(new HostForm(parentHandle));
            return 0;
        }
    }
}