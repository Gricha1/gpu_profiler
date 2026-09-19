// GPU Profiler launcher — no third-party deps (.NET Framework 4.x / csc).
// Finds project root relative to this EXE (../ from launchers/).
using System;
using System.Diagnostics;
using System.IO;
using System.Net.Sockets;
using System.Threading;
using System.Windows.Forms;

internal static class Program
{
    const string Host = "127.0.0.1";
    const int Port = 8765;
    const string Url = "http://127.0.0.1:8765/";
    const string MutexName = "Local\\GPUProfiler.Launcher.8765";

    [STAThread]
    static int Main()
    {
        Application.EnableVisualStyles();
        bool createdNew;
        using (var mutex = new Mutex(true, MutexName, out createdNew))
        {
            try
            {
                if (!createdNew)
                {
                    // Another launcher is starting/opening — just focus UI if up.
                    if (PortOpen())
                        OpenUrl();
                    return 0;
                }

                string root = FindProjectRoot();
                if (root == null)
                {
                    MessageBox.Show(
                        "Cannot find GPU Profiler project (scripts\\start.ps1).\n" +
                        "Keep this EXE in <project>\\launchers\\.",
                        "GPU Profiler",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Error);
                    return 1;
                }

                if (PortOpen())
                {
                    OpenUrl();
                    return 0;
                }

                string startPs1 = Path.Combine(root, "scripts", "start.ps1");
                var psi = new ProcessStartInfo
                {
                    FileName = "powershell.exe",
                    Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"" + startPs1 + "\" -NoBrowser",
                    WorkingDirectory = root,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    WindowStyle = ProcessWindowStyle.Hidden
                };
                using (var p = Process.Start(psi))
                {
                    if (p == null)
                    {
                        MessageBox.Show("Failed to start start.ps1", "GPU Profiler",
                            MessageBoxButtons.OK, MessageBoxIcon.Error);
                        return 1;
                    }
                }

                if (!WaitHttpReady(45000))
                {
                    MessageBox.Show(
                        "GPU Profiler did not become ready.\nSee logs\\uvicorn.err.log",
                        "GPU Profiler",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Error);
                    return 1;
                }

                OpenUrl();
                return 0;
            }
            finally
            {
                if (createdNew)
                {
                    try { mutex.ReleaseMutex(); } catch { }
                }
            }
        }
    }

    static string FindProjectRoot()
    {
        string dir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\', '/');
        // launchers\GPU Profiler.exe -> project root is parent
        string candidate = Path.GetFullPath(Path.Combine(dir, ".."));
        if (File.Exists(Path.Combine(candidate, "scripts", "start.ps1")))
            return candidate;
        // Also allow EXE sitting in project root
        if (File.Exists(Path.Combine(dir, "scripts", "start.ps1")))
            return dir;
        return null;
    }

    static bool PortOpen()
    {
        try
        {
            using (var c = new TcpClient())
            {
                var ar = c.BeginConnect(Host, Port, null, null);
                bool ok = ar.AsyncWaitHandle.WaitOne(300);
                if (ok && c.Connected) return true;
            }
        }
        catch { }
        return false;
    }

    static bool WaitHttpReady(int timeoutMs)
    {
        int deadline = Environment.TickCount + timeoutMs;
        while (Environment.TickCount < deadline)
        {
            if (PortOpen())
            {
                try
                {
                    var req = (System.Net.HttpWebRequest)System.Net.WebRequest.Create(Url);
                    req.Timeout = 2000;
                    req.Method = "GET";
                    using (var resp = (System.Net.HttpWebResponse)req.GetResponse())
                    {
                        if ((int)resp.StatusCode >= 200 && (int)resp.StatusCode < 500)
                            return true;
                    }
                }
                catch { }
            }
            Thread.Sleep(400);
        }
        return false;
    }

    static void OpenUrl()
    {
        Process.Start(new ProcessStartInfo { FileName = Url, UseShellExecute = true });
    }
}
