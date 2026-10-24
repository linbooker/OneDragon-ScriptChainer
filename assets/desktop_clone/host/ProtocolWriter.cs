using System;
using System.IO;
using System.Text;

namespace OneDragonScriptChainer.DesktopClone
{
    /// <summary>
    /// 宿主进程与 Python 端（QProcess）之间的单行文本协议。
    ///
    /// 输出：ready / state N / session N / launched PID / message BASE64 / error BASE64 / bye
    /// 输入：connect W H / disconnect / logoff / launch EXE_B64 [ARGS_B64] [DIR_B64] /
    ///       shortcut wind|wintab / smartsizing 0|1 / audiomute 0|1 / syskeys 0|1 /
    ///       input 0|1 / status / quit
    ///
    /// 文本类消息统一 base64 一次，避免中文与换行在管道编码上出岔子。
    /// </summary>
    internal sealed class ProtocolWriter
    {
        private readonly StreamWriter _writer;
        private readonly object _lock = new object();

        internal ProtocolWriter()
        {
            _writer = new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false))
            {
                AutoFlush = true
            };
        }

        internal void Write(string line)
        {
            lock (_lock)
            {
                try
                {
                    _writer.Write(line);
                    _writer.Write('\n');
                    _writer.Flush();
                }
                catch (Exception)
                {
                    // 管道另一端已经消失，直接忽略
                }
            }
        }

        internal void Message(string text)
        {
            Write("message " + Encode(text));
        }

        internal void Error(string text)
        {
            Write("error " + Encode(text));
        }

        internal static string Encode(string text)
        {
            if (string.IsNullOrEmpty(text))
            {
                return string.Empty;
            }

            return Convert.ToBase64String(Encoding.UTF8.GetBytes(text));
        }

        internal static string Decode(string text)
        {
            if (string.IsNullOrEmpty(text))
            {
                return string.Empty;
            }

            return Encoding.UTF8.GetString(Convert.FromBase64String(text));
        }
    }
}