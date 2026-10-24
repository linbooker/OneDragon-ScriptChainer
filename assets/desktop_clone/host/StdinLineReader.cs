using System;
using System.Collections.Generic;
using System.Text;

namespace OneDragonScriptChainer.DesktopClone
{
    /// <summary>
    /// 从标准输入按行读取命令。
    /// 不走 Console.OpenStandardInput / StreamReader：在 WinForms 进程里被 QProcess
    /// 用匿名管道拉起时，.NET 的那层包装读不到数据（同句柄上直接 ReadFile 正常），
    /// 所以这里直接调用 Win32 的 ReadFile 自行切行。
    /// </summary>
    internal sealed class StdinLineReader
    {
        private const byte LineFeed = (byte)'\n';

        private readonly IntPtr _handle;
        private readonly byte[] _buffer = new byte[4096];
        private readonly List<byte> _pending = new List<byte>();

        internal StdinLineReader()
        {
            _handle = NativeMethods.GetStdHandle(NativeMethods.StandardInputHandle);
        }

        /// <summary>读一行；输入结束（或句柄无效）时返回 null。</summary>
        internal string ReadLine()
        {
            while (true)
            {
                int index = _pending.IndexOf(LineFeed);
                if (index >= 0)
                {
                    string line = Encoding.UTF8.GetString(_pending.GetRange(0, index).ToArray());
                    _pending.RemoveRange(0, index + 1);
                    return line.TrimEnd('\r');
                }

                if (_handle == IntPtr.Zero || _handle == NativeMethods.InvalidHandleValue)
                {
                    return null;
                }

                int read;
                bool ok = NativeMethods.ReadFile(
                    _handle, _buffer, _buffer.Length, out read, IntPtr.Zero);
                if (!ok || read <= 0)
                {
                    return null;
                }

                for (int i = 0; i < read; i++)
                {
                    _pending.Add(_buffer[i]);
                }
            }
        }
    }
}