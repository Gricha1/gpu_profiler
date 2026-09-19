// Mesh Watcher launcher — start/check Task Scheduler job only (no parallel watcher).
// Uses Schedule.Service COM (no NuGet). .NET Framework 4.x / csc.
using System;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Windows.Forms;

internal static class Program
{
    const string TaskName = "GPUProfiler-MeshRouteWatcher";
    // TASK_STATE: Unknown=0, Disabled=1, Queued=2, Ready=3, Running=4
    const int TaskStateRunning = 4;

    [STAThread]
    static int Main()
    {
        Application.EnableVisualStyles();
        try
        {
            Type svcType = Type.GetTypeFromProgID("Schedule.Service");
            if (svcType == null)
            {
                Fail("Task Scheduler COM (Schedule.Service) unavailable.");
                return 1;
            }

            object service = Activator.CreateInstance(svcType);
            svcType.InvokeMember("Connect", BindingFlags.InvokeMethod, null, service, new object[] { });

            object folder = svcType.InvokeMember("GetFolder", BindingFlags.InvokeMethod, null, service,
                new object[] { "\\" });

            object task;
            try
            {
                task = folder.GetType().InvokeMember("GetTask", BindingFlags.InvokeMethod, null, folder,
                    new object[] { TaskName });
            }
            catch (TargetInvocationException ex)
            {
                // 0x80070002 / not found
                MessageBox.Show(
                    "Watcher task is not installed.\n\n" +
                    "Task name: " + TaskName + "\n" +
                    "Install with (Admin):\n" +
                    "  scripts\\amnezia\\install_mesh_route_watcher_task.ps1\n\n" +
                    Detail(ex),
                    "Mesh Watcher",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
                return 1;
            }

            int state = Convert.ToInt32(
                task.GetType().InvokeMember("State", BindingFlags.GetProperty, null, task, null));

            if (state == TaskStateRunning)
            {
                MessageBox.Show(
                    "Watcher already running",
                    "Mesh Watcher",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Information);
                return 0;
            }

            // Ready / Queued / Disabled-but-present: try Run
            task.GetType().InvokeMember("Run", BindingFlags.InvokeMethod, null, task, new object[] { null });

            MessageBox.Show(
                "Watcher started\n\nTask: " + TaskName + "\n(At logon remains enabled)",
                "Mesh Watcher",
                MessageBoxButtons.OK,
                MessageBoxIcon.Information);
            return 0;
        }
        catch (Exception ex)
        {
            Fail(ex.Message);
            return 1;
        }
    }

    static void Fail(string msg)
    {
        MessageBox.Show(msg, "Mesh Watcher", MessageBoxButtons.OK, MessageBoxIcon.Error);
    }

    static string Detail(Exception ex)
    {
        Exception e = ex;
        while (e.InnerException != null) e = e.InnerException;
        return e.Message;
    }
}
