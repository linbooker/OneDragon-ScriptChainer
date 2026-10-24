using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Principal;

namespace OneDragonScriptChainer.DesktopClone
{
    /// <summary>
    /// 把程序启动到「桌面分身」（子会话）里。
    ///
    /// 子会话桌面上没有资源管理器可以双击，普通 Process.Start 也只会落在当前会话，
    /// 因此这里和 BetterGenshinImpact 一样走任务计划程序 COM：注册一个临时任务，
    /// 用 RunEx 的 TASK_RUN_USE_SESSION_ID 直接把任务丢进子会话运行，最后删掉任务。
    /// </summary>
    internal static class SessionLauncher
    {
        private const string TaskNamePrefix = "OneDragonScriptChainerDesktopClone";
        private const int RunWaitMillis = 3000;
        private const int PollIntervalMillis = 150;

        private const int TaskActionExecute = 0;
        private const int TaskCreate = 2;
        private const int TaskLogonInteractiveToken = 3;
        private const int TaskRunLevelHighest = 1;
        private const int TaskRunUseSessionId = 0x4;

        /// <summary>在子会话中启动目标程序，返回新进程的 Id（找不到进程时返回 0）。</summary>
        internal static int Launch(uint childSessionId, string exePath, string arguments, string workDirectory)
        {
            if (childSessionId == 0)
            {
                throw new InvalidOperationException("桌面分身尚未建立子会话，无法启动程序");
            }

            if (!File.Exists(exePath))
            {
                throw new FileNotFoundException("找不到要启动的程序：" + exePath, exePath);
            }

            string workingDirectory = string.IsNullOrEmpty(workDirectory)
                ? (Path.GetDirectoryName(exePath) ?? string.Empty)
                : workDirectory;

            RunInChildSession(childSessionId, exePath, arguments ?? string.Empty, workingDirectory);
            return WaitForProcess(exePath, childSessionId);
        }

        /// <summary>注册临时任务并用 RunEx 在指定会话里启动，随后删除任务。</summary>
        private static void RunInChildSession(
            uint childSessionId,
            string exePath,
            string arguments,
            string workingDirectory)
        {
            Type schedulerType = Type.GetTypeFromProgID("Schedule.Service");
            if (schedulerType == null)
            {
                throw new InvalidOperationException("当前系统没有任务计划程序 COM 服务");
            }

            string taskName = TaskNamePrefix + "_" + Guid.NewGuid().ToString("N");
            string accountName;
            using (WindowsIdentity identity = WindowsIdentity.GetCurrent())
            {
                accountName = identity.Name;
            }

            object schedulerObject = null;
            object rootFolderObject = null;
            object taskDefinitionObject = null;
            object actionObject = null;
            object registeredTaskObject = null;
            object runningTaskObject = null;
            bool taskRegistered = false;

            try
            {
                schedulerObject = Activator.CreateInstance(schedulerType);
                if (schedulerObject == null)
                {
                    throw new InvalidOperationException("无法创建任务计划程序对象");
                }

                dynamic scheduler = schedulerObject;
                scheduler.Connect();

                rootFolderObject = scheduler.GetFolder("\\");
                dynamic rootFolder = rootFolderObject;
                taskDefinitionObject = scheduler.NewTask(0);
                dynamic taskDefinition = taskDefinitionObject;

                taskDefinition.RegistrationInfo.Author = "OneDragon-ScriptChainer";
                taskDefinition.RegistrationInfo.Description =
                    "临时启动 " + Path.GetFileName(exePath) + " 到桌面分身会话 " + childSessionId;

                taskDefinition.Settings.Enabled = true;
                taskDefinition.Settings.Hidden = true;
                taskDefinition.Settings.AllowDemandStart = true;
                taskDefinition.Settings.DisallowStartIfOnBatteries = false;
                taskDefinition.Settings.StopIfGoingOnBatteries = false;
                taskDefinition.Settings.ExecutionTimeLimit = "PT0S";

                taskDefinition.Principal.UserId = accountName;
                taskDefinition.Principal.LogonType = TaskLogonInteractiveToken;
                taskDefinition.Principal.RunLevel = TaskRunLevelHighest;

                actionObject = taskDefinition.Actions.Create(TaskActionExecute);
                dynamic action = actionObject;
                action.Path = exePath;
                action.Arguments = arguments;
                action.WorkingDirectory = workingDirectory;

                registeredTaskObject = rootFolder.RegisterTaskDefinition(
                    taskName,
                    taskDefinition,
                    TaskCreate,
                    accountName,
                    null,
                    TaskLogonInteractiveToken,
                    null);
                taskRegistered = true;

                dynamic registeredTask = registeredTaskObject;
                runningTaskObject = registeredTask.RunEx(
                    null,
                    TaskRunUseSessionId,
                    checked((int)childSessionId),
                    null);

                if (runningTaskObject == null)
                {
                    throw new InvalidOperationException("任务计划程序没有返回运行实例");
                }
            }
            catch (COMException e)
            {
                throw new InvalidOperationException(
                    "计划任务启动失败（0x" + e.HResult.ToString("X8") + "）：" + e.Message, e);
            }
            finally
            {
                if (taskRegistered && rootFolderObject != null)
                {
                    try
                    {
                        dynamic rootFolder = rootFolderObject;
                        rootFolder.DeleteTask(taskName, 0);
                    }
                    catch (Exception)
                    {
                        // 任务启动成功后清理失败不影响已经跑起来的程序
                    }
                }

                ReleaseComObject(runningTaskObject);
                ReleaseComObject(registeredTaskObject);
                ReleaseComObject(actionObject);
                ReleaseComObject(taskDefinitionObject);
                ReleaseComObject(rootFolderObject);
                ReleaseComObject(schedulerObject);
            }
        }

        private static void ReleaseComObject(object value)
        {
            if (value != null && Marshal.IsComObject(value))
            {
                Marshal.FinalReleaseComObject(value);
            }
        }

        /// <summary>任务启动后按「可执行文件路径 + 会话 id」找到新进程。</summary>
        private static int WaitForProcess(string exePath, uint childSessionId)
        {
            string exeName = Path.GetFileNameWithoutExtension(exePath);
            DateTime deadline = DateTime.Now.AddMilliseconds(RunWaitMillis);
            int bestPid = 0;
            DateTime bestStart = DateTime.MinValue;

            while (DateTime.Now < deadline)
            {
                foreach (Process process in FindProcesses(exeName))
                {
                    try
                    {
                        if ((uint)process.SessionId != childSessionId)
                        {
                            continue;
                        }

                        string processPath = TryGetProcessPath(process);
                        if (!string.IsNullOrEmpty(processPath)
                            && !string.Equals(processPath, exePath, StringComparison.OrdinalIgnoreCase))
                        {
                            continue;
                        }

                        DateTime startedAt = process.StartTime;
                        if (startedAt > bestStart)
                        {
                            bestStart = startedAt;
                            bestPid = process.Id;
                        }
                    }
                    catch (Exception)
                    {
                        // 进程可能已经退出，忽略
                    }
                }

                if (bestPid != 0)
                {
                    return bestPid;
                }

                System.Threading.Thread.Sleep(PollIntervalMillis);
            }

            return 0;
        }

        private static IEnumerable<Process> FindProcesses(string processName)
        {
            try
            {
                return Process.GetProcessesByName(processName);
            }
            catch (Exception)
            {
                return new Process[0];
            }
        }

        private static string TryGetProcessPath(Process process)
        {
            try
            {
                return process.MainModule == null ? string.Empty : process.MainModule.FileName;
            }
            catch (Exception)
            {
                return string.Empty;
            }
        }

        /// <summary>给调试/日志用的任务名格式说明。</summary>
        internal static string DescribeTaskNamePattern()
        {
            return TaskNamePrefix + "_{guid}";
        }
    }
}