// ScriptHub.exe: the launcher of the portable Windows build (tools/build_portable.py).
// It starts the bundled Python (python\pythonw.exe, no console) with desktop.pyw next to it
// and passes its own arguments on (for example --background). Nothing else: no installer,
// no registry, no network. Compiled with the C# compiler that ships with Windows (.NET Framework 4).
using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

[assembly: AssemblyTitle("ScriptHub")]
[assembly: AssemblyProduct("ScriptHub")]
[assembly: AssemblyDescription("Run and monitor your scripts (portable launcher)")]

static class Launcher
{
    [STAThread]
    static int Main(string[] args)
    {
        string dir = AppDomain.CurrentDomain.BaseDirectory;
        string python = Path.Combine(dir, "python", "pythonw.exe");
        string app = Path.Combine(dir, "desktop.pyw");
        if (!File.Exists(python) || !File.Exists(app))
        {
            MessageBox.Show("ScriptHub is incomplete: python\\pythonw.exe or desktop.pyw is missing next to ScriptHub.exe.\n\n"
                            + "Unzip the whole download again and start ScriptHub.exe from that folder.",
                            "ScriptHub", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        var cmd = new StringBuilder(Quote(app));
        foreach (string a in args) cmd.Append(' ').Append(Quote(a));
        var start = new ProcessStartInfo(python, cmd.ToString());
        start.WorkingDirectory = dir;
        start.UseShellExecute = false;
        try
        {
            Process.Start(start);
            return 0;
        }
        catch (Exception e)
        {
            MessageBox.Show("ScriptHub could not start its Python:\n\n" + e.Message, "ScriptHub",
                            MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }

    // One argument for the Windows command line (the rules CommandLineToArgvW and the C runtime use).
    static string Quote(string arg)
    {
        if (arg.Length > 0 && arg.IndexOfAny(new[] { ' ', '\t', '"' }) < 0) return arg;
        var sb = new StringBuilder("\"");
        int slashes = 0;
        foreach (char c in arg)
        {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') sb.Append('\\', slashes * 2 + 1).Append('"');
            else sb.Append('\\', slashes).Append(c);
            slashes = 0;
        }
        sb.Append('\\', slashes * 2).Append('"');
        return sb.ToString();
    }
}
