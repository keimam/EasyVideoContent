// Native Windows GUI Launcher Stub for EasyFaceless Studio (Stage 8C.2, D3)
// Compiled with: csc.exe /target:winexe /win32icon:app.ico LauncherStub.cs
// Launches bundled Python + Streamlit launcher_runtime without flashing a console window.

using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyTitle("EasyFaceless Studio")]
[assembly: AssemblyDescription("EasyFaceless Studio Desktop Launcher")]
[assembly: AssemblyCompany("Naseh Ngulik")]
[assembly: AssemblyProduct("EasyFaceless Studio")]
[assembly: AssemblyCopyright("Copyright (c) 2026 Naseh Ngulik")]
[assembly: AssemblyVersion("1.0.2.0")]
[assembly: AssemblyFileVersion("1.0.2.0")]

namespace EasyFacelessStudioLauncher
{
    internal static class Program
    {
        [STAThread]
        private static int Main()
        {
            try
            {
                string installRoot = Path.GetFullPath(AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\', '/'));
                string pythonwExe = Path.Combine(installRoot, "runtime", "python", "pythonw.exe");
                string pythonExe = Path.Combine(installRoot, "runtime", "python", "python.exe");
                string targetExe = File.Exists(pythonwExe) ? pythonwExe : pythonExe;

                if (!File.Exists(targetExe))
                {
                    MessageBox.Show(
                        "Runtime Python tidak ditemukan di folder instalasi:\n" + targetExe +
                        "\n\nSilakan instal ulang EasyFaceless Studio.",
                        "EasyFaceless Studio",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Error
                    );
                    return 1;
                }

                string dataDir = Environment.GetEnvironmentVariable("EFS_DATA_DIR");
                if (string.IsNullOrWhiteSpace(dataDir))
                {
                    string localApp = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                    dataDir = Path.Combine(localApp, "EasyFaceless Studio");
                }
                string logsDir = Path.Combine(dataDir, "logs");
                Directory.CreateDirectory(logsDir);

                string ffBin = Path.Combine(installRoot, "runtime", "ffmpeg", "bin");
                string pyDir = Path.Combine(installRoot, "runtime", "python");
                string pyScripts = Path.Combine(pyDir, "Scripts");
                string existingPath = Environment.GetEnvironmentVariable("PATH") ?? "";

                var psi = new ProcessStartInfo
                {
                    FileName = targetExe,
                    Arguments = "-m app.services.longform.launcher_runtime",
                    WorkingDirectory = installRoot,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    WindowStyle = ProcessWindowStyle.Hidden,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                };

                psi.EnvironmentVariables["EFS_INSTALLED"] = "1";
                psi.EnvironmentVariables["EFS_DATA_DIR"] = dataDir;
                psi.EnvironmentVariables["PYTHONHOME"] = pyDir;
                psi.EnvironmentVariables["PYTHONPATH"] = installRoot;
                psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
                psi.EnvironmentVariables["PATH"] = ffBin + ";" + pyDir + ";" + pyScripts + ";" + existingPath;

                using (var proc = Process.Start(psi))
                {
                    if (proc == null)
                    {
                        MessageBox.Show(
                            "Gagal memulai launcher EasyFaceless Studio.",
                            "EasyFaceless Studio",
                            MessageBoxButtons.OK,
                            MessageBoxIcon.Error
                        );
                        return 1;
                    }

                    string stdoutText = proc.StandardOutput.ReadToEnd();
                    string stderrText = proc.StandardError.ReadToEnd();
                    proc.WaitForExit();

                    if (!string.IsNullOrWhiteSpace(stdoutText) || !string.IsNullOrWhiteSpace(stderrText))
                    {
                        string stubLog = Path.Combine(logsDir, "launcher_stub.log");
                        File.AppendAllText(
                            stubLog,
                            "[" + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "] exit=" + proc.ExitCode +
                            Environment.NewLine + stdoutText + stderrText + Environment.NewLine
                        );
                    }

                    if (proc.ExitCode != 0)
                    {
                        MessageBox.Show(
                            "EasyFaceless Studio gagal dijalankan (kode " + proc.ExitCode + ").\n\n" +
                            "Lihat rincian log di:\n" + Path.Combine(logsDir, "launcher.log"),
                            "EasyFaceless Studio",
                            MessageBoxButtons.OK,
                            MessageBoxIcon.Error
                        );
                        return proc.ExitCode;
                    }
                }
                return 0;
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Kesalahan saat membuka EasyFaceless Studio:\n" + ex.Message,
                    "EasyFaceless Studio",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
                return 1;
            }
        }
    }
}
