using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;

namespace SheetXML
{
    static class Program
    {
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool AttachConsole(int dwProcessId);

        private const int ATTACH_PARENT_PROCESS = -1;
        private const int SERVER_PORT = 5050;
        private static readonly string SERVER_HEALTH_URL = "http://127.0.0.1:" + SERVER_PORT + "/api/config";
        private static readonly string BROWSER_URL = "http://localhost:" + SERVER_PORT;

        [STAThread]
        static void Main(string[] args)
        {
            string appDir = AppDomain.CurrentDomain.BaseDirectory;
            string pythonScript = Path.Combine(appDir, "app.py");

            if (!File.Exists(pythonScript))
            {
                MessageBox.Show("Could not find app.py in:\n" + appDir, "SheetXML Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return;
            }

            string pythonExe = FindPythonExecutable();

            // ==========================================
            // CLI MODE (When command line args provided)
            // ==========================================
            if (args.Length > 0)
            {
                AttachConsole(ATTACH_PARENT_PROCESS);

                ProcessStartInfo psiCli = new ProcessStartInfo();
                psiCli.FileName = pythonExe;
                psiCli.WorkingDirectory = appDir;

                string argStr = "";
                for (int i = 0; i < args.Length; i++)
                {
                    argStr += " \"" + args[i] + "\"";
                }
                psiCli.Arguments = "-B \"" + pythonScript + "\"" + argStr;
                psiCli.UseShellExecute = false;
                psiCli.RedirectStandardOutput = true;
                psiCli.RedirectStandardError = true;

                try
                {
                    using (Process proc = Process.Start(psiCli))
                    {
                        if (proc != null)
                        {
                            proc.OutputDataReceived += (s, e) => {
                                if (e.Data != null) Console.Out.WriteLine(e.Data);
                            };
                            proc.ErrorDataReceived += (s, e) => {
                                if (e.Data != null) Console.Error.WriteLine(e.Data);
                            };
                            proc.BeginOutputReadLine();
                            proc.BeginErrorReadLine();
                            proc.WaitForExit();
                        }
                    }
                }
                catch (Exception ex)
                {
                    Console.Error.WriteLine("[SheetXML CLI Error] " + ex.Message);
                }
                return;
            }

            WebRequest.DefaultWebProxy = null;

            // ==========================================
            // GUI MODE (Double-clicked shortcut)
            // ==========================================

            // Step 1: Check if an active, healthy SheetXML server is already running
            if (IsServerAlive(SERVER_HEALTH_URL, 1500))
            {
                // Server is already alive and responding, simply open browser and exit
                OpenBrowser(BROWSER_URL);
                return;
            }

            // Step 2: Clean up any stale or orphaned background process holding the port
            CleanStaleServer(appDir, SERVER_PORT);

            // Step 3: Launch new background server process
            ProcessStartInfo psi = new ProcessStartInfo();
            psi.FileName = pythonExe;
            psi.WorkingDirectory = appDir;
            psi.Arguments = "-B \"" + pythonScript + "\" --port " + SERVER_PORT + " --no-browser";
            psi.UseShellExecute = true;
            psi.WindowStyle = ProcessWindowStyle.Hidden;

            Process serverProc = null;
            try
            {
                serverProc = Process.Start(psi);
            }
            catch (Exception ex)
            {
                MessageBox.Show("Failed to launch Python engine (" + pythonExe + "):\n" + ex.Message, "SheetXML Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return;
            }

            // Step 4: Poll server health check before opening browser
            // Ensures the browser never receives ERR_CONNECTION_REFUSED or ERR_EMPTY_RESPONSE
            bool serverReady = false;
            DateTime deadline = DateTime.Now.AddSeconds(8);

            while (DateTime.Now < deadline)
            {
                Thread.Sleep(150);

                if (serverProc != null && serverProc.HasExited)
                {
                    string logFile = Path.Combine(appDir, "sheetxml.log");
                    string logSnippet = "";
                    if (File.Exists(logFile))
                    {
                        try { logSnippet = "\n\nLog details:\n" + File.ReadAllText(logFile); } catch { }
                    }
                    MessageBox.Show("SheetXML server process stopped unexpectedly (Code: " + serverProc.ExitCode + ")." + logSnippet, "SheetXML Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    return;
                }

                if (IsServerAlive(SERVER_HEALTH_URL, 500))
                {
                    serverReady = true;
                    break;
                }
            }

            // Step 5: Open default web browser once verified
            if (serverReady)
            {
                OpenBrowser(BROWSER_URL);
            }
            else
            {
                // If it took longer than 8s, attempt opening anyway but inform user
                OpenBrowser(BROWSER_URL);
            }
        }

        static bool IsServerAlive(string url, int timeoutMs)
        {
            try
            {
                HttpWebRequest req = (HttpWebRequest)WebRequest.Create(url);
                req.Proxy = null;
                req.Timeout = timeoutMs;
                req.ReadWriteTimeout = timeoutMs;
                req.Method = "GET";
                req.KeepAlive = false;
                using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
                {
                    return resp.StatusCode == HttpStatusCode.OK;
                }
            }
            catch
            {
                return false;
            }
        }

        static void CleanStaleServer(string appDir, int port)
        {
            if (IsServerAlive(SERVER_HEALTH_URL, 1000))
            {
                return;
            }

            // Check PID file from previous run
            try
            {
                string pidFile = Path.Combine(appDir, "sheetxml.pid");
                if (File.Exists(pidFile))
                {
                    string content = File.ReadAllText(pidFile).Trim();
                    int pid;
                    if (int.TryParse(content, out pid))
                    {
                        try
                        {
                            Process p = Process.GetProcessById(pid);
                            if (p != null && !p.HasExited)
                            {
                                p.Kill();
                                p.WaitForExit(1000);
                            }
                        }
                        catch { }
                    }
                    try { File.Delete(pidFile); } catch { }
                }
            }
            catch { }

            // Also ensure port is not blocked by a hung zombie process
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = "powershell.exe";
                psi.Arguments = "-NoProfile -Command \"Get-NetTCPConnection -LocalPort " + port + " -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }\"";
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                using (Process proc = Process.Start(psi))
                {
                    if (proc != null) proc.WaitForExit(2000);
                }
            }
            catch { }
        }

        static string FindPythonExecutable()
        {
            string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            string[] preferred = new string[]
            {
                Path.Combine(localAppData, @"Python\pythoncore-3.14-64\python.exe"),
                Path.Combine(localAppData, @"Python\bin\python.exe"),
                Path.Combine(localAppData, @"Programs\Python\Python314\python.exe"),
                Path.Combine(localAppData, @"Programs\Python\Python313\python.exe"),
                Path.Combine(localAppData, @"Programs\Python\Python312\python.exe"),
                Path.Combine(localAppData, @"Programs\Python\Python311\python.exe"),
                @"C:\Python314\python.exe",
                @"C:\Python313\python.exe",
                @"C:\Python312\python.exe"
            };

            foreach (string p in preferred)
            {
                if (File.Exists(p)) return p;
            }

            // Search PATH, preferring standard Python installs over WindowsApps alias
            string pathEnv = Environment.GetEnvironmentVariable("PATH") ?? "";
            string[] dirs = pathEnv.Split(';');
            string fallback = null;

            foreach (string dir in dirs)
            {
                string trimmed = dir.Trim();
                if (string.IsNullOrEmpty(trimmed)) continue;
                string candidate = Path.Combine(trimmed, "python.exe");
                if (File.Exists(candidate))
                {
                    if (candidate.IndexOf("WindowsApps", StringComparison.OrdinalIgnoreCase) >= 0)
                    {
                        if (fallback == null) fallback = candidate;
                    }
                    else
                    {
                        return candidate;
                    }
                }
            }

            return fallback ?? "python.exe";
        }

        static void OpenBrowser(string url)
        {
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo(url);
                psi.UseShellExecute = true;
                Process.Start(psi);
            }
            catch
            {
                try
                {
                    Process.Start("cmd.exe", "/c start " + url);
                }
                catch { }
            }
        }
    }
}
