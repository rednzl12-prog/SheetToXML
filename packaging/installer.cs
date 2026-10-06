// SheetXML per-user installer and uninstaller (one source, C# 5 for the csc that ships with Windows).
// packaging/build.py compiles it twice: uninstall.exe (no resources, goes into the program folder) and
// SheetXML-Setup-<version>.exe (embeds app.zip, the Audiveris MSI and license.txt).
// Flags: /S silent, /D=<dir> install folder, /NOAUDIVERIS, /NODESKTOP; uninstall.exe /uninstall [/S].
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

namespace SheetXMLSetup
{
    static class Setup
    {
        const string App = "SheetXML";
        const string Publisher = "rednzl12-prog";
        const string UninstKey = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\SheetXML";
        const string Manifest = "files.txt";
        static readonly string[] Exes = { "SheetXML", "SheetXML-cli" };
        static readonly string LocalData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        public static readonly string DefaultDir = InstalledDir() ?? Path.Combine(LocalData, "Programs", App);
        public static readonly string DataDir = Path.Combine(LocalData, App);
        static readonly string MenuDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), App);
        static readonly string DesktopLnk = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), App + ".lnk");
        public static readonly string AudiverisExe = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "Audiveris", "Audiveris.exe");
        static readonly Assembly Me = Assembly.GetExecutingAssembly();
        public static string Version
        {
            get { return ((AssemblyInformationalVersionAttribute)Attribute.GetCustomAttribute(Me, typeof(AssemblyInformationalVersionAttribute))).InformationalVersion; }
        }

        [STAThread]
        static int Main(string[] args)
        {
            bool silent = Has(args, "/S"), uninstall = Has(args, "/uninstall");
            string dir = DefaultDir;
            foreach (string a in args)
                if (a.StartsWith("/D=", StringComparison.OrdinalIgnoreCase)) dir = a.Substring(3).Trim('"');
            Application.EnableVisualStyles();
            try
            {
                if (uninstall || Me.GetManifestResourceInfo("app.zip") == null)
                    return Uninstall(silent);
                if (!silent)
                {
                    Application.Run(new Wizard());
                    return 0;
                }
                string msg = Install(Path.GetFullPath(dir), !Has(args, "/NODESKTOP"),
                                     !Has(args, "/NOAUDIVERIS") && !File.Exists(AudiverisExe), null);
                Log(msg);
                return 0;
            }
            catch (Exception e)
            {
                Log("ERROR: " + e);
                if (!silent) MessageBox.Show(e.Message, App + " Setup", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 1;
            }
        }

        /// <summary>Folder of an existing install (upgrades go there by default), or null.</summary>
        static string InstalledDir()
        {
            using (RegistryKey k = Registry.CurrentUser.OpenSubKey(UninstKey))
                return k == null ? null : k.GetValue("InstallLocation") as string;
        }

        static bool Has(string[] args, string flag)
        {
            return args.Any(a => string.Equals(a, flag, StringComparison.OrdinalIgnoreCase));
        }

        public static void Log(string msg)
        {
            try { File.AppendAllText(Path.Combine(Path.GetTempPath(), "SheetXML-setup.log"), DateTime.Now.ToString("s") + " " + msg + "\r\n"); }
            catch { }
        }

        public static string LicenseText()
        {
            using (Stream s = Me.GetManifestResourceStream("license.txt"))
            using (StreamReader r = new StreamReader(s))
                return r.ReadToEnd().Replace("\r\n", "\n").Replace("\n", "\r\n");
        }

        // ---------------------------------------------------------------- install

        /// <summary>Installs (or upgrades) into dir; returns a one-line summary. progress(text, percent) may be null.</summary>
        public static string Install(string dir, bool desktop, bool audiveris, Action<string, int> progress)
        {
            if (progress == null) progress = (t, p) => { };
            progress("Closing a running SheetXML...", 0);
            StopRunning(dir);
            Directory.CreateDirectory(dir);
            DeleteListed(dir);  // files of a previous version

            List<string> files = new List<string>();
            long total = 0;
            try
            {
                using (Stream s = Me.GetManifestResourceStream("app.zip"))
                using (ZipArchive zip = new ZipArchive(s, ZipArchiveMode.Read))
                {
                    long all = Math.Max(1, zip.Entries.Sum(e => e.Length)), done = 0;
                    string root = Path.GetFullPath(dir).TrimEnd('\\') + "\\";
                    foreach (ZipArchiveEntry e in zip.Entries)
                    {
                        string target = Path.GetFullPath(Path.Combine(root, e.FullName));
                        if (!target.StartsWith(root, StringComparison.OrdinalIgnoreCase)) continue;
                        if (e.FullName.EndsWith("/")) { Directory.CreateDirectory(target); continue; }
                        Directory.CreateDirectory(Path.GetDirectoryName(target));
                        using (Stream src = e.Open())
                        using (FileStream dst = File.Create(target))
                            src.CopyTo(dst);
                        files.Add(e.FullName.Replace('/', '\\'));
                        done += e.Length;
                        total += e.Length;
                        progress("Copying " + e.Name, (int)(80 * done / all));
                    }
                }
            }
            finally { File.WriteAllLines(Path.Combine(dir, Manifest), files); }  // a failed copy stays uninstallable

            progress("Creating shortcuts...", 85);
            string gui = Path.Combine(dir, "SheetXML.exe"), cli = Path.Combine(dir, "SheetXML-cli.exe");
            string uninst = Path.Combine(dir, "uninstall.exe");
            Directory.CreateDirectory(MenuDir);
            Shortcut(Path.Combine(MenuDir, "SheetXML.lnk"), gui, "", dir, gui, "Sheet music to MusicXML + mandolin TAB");
            Shortcut(Path.Combine(MenuDir, "SheetXML Command Line.lnk"), Environment.ExpandEnvironmentVariables(@"%ComSpec%"),
                     "/k set \"PATH=" + dir + ";%PATH%\" & SheetXML-cli --help",
                     Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), cli, "SheetXML-cli in a command prompt");
            Shortcut(Path.Combine(MenuDir, "Uninstall SheetXML.lnk"), uninst, "/uninstall", dir, uninst, "Remove SheetXML");
            if (desktop) Shortcut(DesktopLnk, gui, "", dir, gui, "Sheet music to MusicXML + mandolin TAB");

            using (RegistryKey k = Registry.CurrentUser.CreateSubKey(UninstKey))
            {
                k.SetValue("DisplayName", App);
                k.SetValue("DisplayVersion", Version);
                k.SetValue("Publisher", Publisher);
                k.SetValue("InstallLocation", dir);
                k.SetValue("DisplayIcon", gui);
                k.SetValue("UninstallString", "\"" + uninst + "\" /uninstall");
                k.SetValue("QuietUninstallString", "\"" + uninst + "\" /uninstall /S");
                k.SetValue("EstimatedSize", (int)(total / 1024), RegistryValueKind.DWord);
                k.SetValue("NoModify", 1, RegistryValueKind.DWord);
                k.SetValue("NoRepair", 1, RegistryValueKind.DWord);
            }
            string msg = "SheetXML " + Version + " installed in " + dir + ".";
            if (audiveris)
            {
                progress("Installing Audiveris (approve the Windows prompt)...", 90);
                msg += " " + InstallAudiveris();
            }
            progress("Done.", 100);
            return msg;
        }

        static string InstallAudiveris()
        {
            string res = Me.GetManifestResourceNames().FirstOrDefault(n => n.EndsWith(".msi", StringComparison.OrdinalIgnoreCase));
            if (res == null) return "Audiveris installer not included in this build.";
            string msi = Path.Combine(Path.GetTempPath(), res);
            try
            {
                using (Stream s = Me.GetManifestResourceStream(res))
                using (FileStream f = File.Create(msi))
                    s.CopyTo(f);
                ProcessStartInfo psi = new ProcessStartInfo("msiexec.exe", "/i \"" + msi + "\" /passive /norestart");  // basic UI reboots unasked otherwise
                psi.UseShellExecute = true;
                psi.Verb = "runas";  // Audiveris installs per machine: one UAC prompt
                using (Process p = Process.Start(psi))
                {
                    p.WaitForExit();
                    if (p.ExitCode == 0) return "Audiveris installed.";
                    if (p.ExitCode == 3010) return "Audiveris installed (restart Windows to finish).";
                    if (p.ExitCode == 1602) return "Audiveris installation was cancelled.";
                    return "Audiveris installer failed (msiexec code " + p.ExitCode + ").";
                }
            }
            catch (System.ComponentModel.Win32Exception)
            {
                return "Audiveris was not installed (administrator approval declined). Run Setup again to retry.";
            }
            finally
            {
                try { File.Delete(msi); } catch { }
            }
        }

        static void Shortcut(string lnk, string target, string args, string workDir, string icon, string description)
        {
            Type t = Type.GetTypeFromProgID("WScript.Shell");
            dynamic shell = Activator.CreateInstance(t);
            dynamic s = shell.CreateShortcut(lnk);
            s.TargetPath = target;
            s.Arguments = args;
            s.WorkingDirectory = workDir;
            s.IconLocation = icon + ",0";
            s.Description = description;
            s.Save();
        }

        static string ShortcutTarget(string lnk)
        {
            try
            {
                dynamic shell = Activator.CreateInstance(Type.GetTypeFromProgID("WScript.Shell"));
                return (string)shell.CreateShortcut(lnk).TargetPath;
            }
            catch { return ""; }
        }

        /// <summary>Kills SheetXML processes started from dir (a running server would lock its files).</summary>
        static void StopRunning(string dir)
        {
            string root = Path.GetFullPath(dir).TrimEnd('\\') + "\\";
            foreach (string name in Exes)
                foreach (Process p in Process.GetProcessesByName(name))
                {
                    try
                    {
                        if (p.MainModule.FileName.StartsWith(root, StringComparison.OrdinalIgnoreCase))
                        {
                            p.Kill();
                            p.WaitForExit(5000);
                        }
                    }
                    catch { }
                }
        }

        /// <summary>Deletes the files a previous install listed in files.txt, then empty folders. Never touches anything else.</summary>
        static void DeleteListed(string dir)
        {
            string manifest = Path.Combine(dir, Manifest);
            if (!File.Exists(manifest)) return;
            string root = Path.GetFullPath(dir).TrimEnd('\\') + "\\";
            HashSet<string> dirs = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (string rel in File.ReadAllLines(manifest))
            {
                string f = Path.GetFullPath(Path.Combine(root, rel));
                if (rel.Length == 0 || !f.StartsWith(root, StringComparison.OrdinalIgnoreCase)) continue;
                if (f.Equals(Me.Location, StringComparison.OrdinalIgnoreCase)) continue;  // running uninstall.exe
                try { File.Delete(f); } catch { }
                for (string d = Path.GetDirectoryName(f); d.Length > root.Length; d = Path.GetDirectoryName(d)) dirs.Add(d);
            }
            foreach (string d in dirs.OrderByDescending(d => d.Length))
                try { Directory.Delete(d); } catch { }  // only succeeds when empty
            File.Delete(manifest);
        }

        // ---------------------------------------------------------------- uninstall

        static int Uninstall(bool silent)
        {
            string dir = Path.GetDirectoryName(Me.Location);
            if (!File.Exists(Path.Combine(dir, Manifest)))
            {
                if (!silent) MessageBox.Show("SheetXML is not installed in " + dir + ".", App, MessageBoxButtons.OK, MessageBoxIcon.Information);
                return 1;
            }
            bool deleteData = false;
            if (!silent)
            {
                if (MessageBox.Show("Remove SheetXML from " + dir + "?", "Uninstall " + App,
                                    MessageBoxButtons.OKCancel, MessageBoxIcon.Question) != DialogResult.OK) return 1;
                if (Directory.Exists(DataDir))
                    deleteData = MessageBox.Show(
                        "Also delete your SheetXML data (API keys, saved transcriptions, logs) in\n" + DataDir + " ?\n\n" +
                        "Choose No to keep it for a later reinstall.", "Uninstall " + App,
                        MessageBoxButtons.YesNo, MessageBoxIcon.Question, MessageBoxDefaultButton.Button2) == DialogResult.Yes;
            }
            StopRunning(dir);
            string root = dir.TrimEnd('\\') + "\\";
            string menuTarget = ShortcutTarget(Path.Combine(MenuDir, "SheetXML.lnk"));
            if (menuTarget.Length == 0 || menuTarget.StartsWith(root, StringComparison.OrdinalIgnoreCase))  // not another install's
            {
                foreach (string lnk in new[] { "SheetXML.lnk", "SheetXML Command Line.lnk", "Uninstall SheetXML.lnk" })
                    try { File.Delete(Path.Combine(MenuDir, lnk)); } catch { }
                try { Directory.Delete(MenuDir); } catch { }
            }
            if (File.Exists(DesktopLnk) && ShortcutTarget(DesktopLnk).StartsWith(root, StringComparison.OrdinalIgnoreCase))
                try { File.Delete(DesktopLnk); } catch { }
            DeleteListed(dir);
            using (RegistryKey k = Registry.CurrentUser.OpenSubKey(UninstKey))
            {
                string loc = k == null ? null : k.GetValue("InstallLocation") as string;
                if (k != null && (loc == null || string.Equals(loc.TrimEnd('\\'), dir.TrimEnd('\\'), StringComparison.OrdinalIgnoreCase)))
                {
                    k.Close();
                    Registry.CurrentUser.DeleteSubKeyTree(UninstKey, false);
                }
            }
            if (deleteData)
                try { Directory.Delete(DataDir, true); } catch { }
            if (!silent)
                MessageBox.Show("SheetXML was removed." + (deleteData ? "" : "\nYour data was kept in " + DataDir + ".") +
                                "\n\nAudiveris (if installed) was left in place; remove it from Windows Settings > Apps.",
                                "Uninstall " + App, MessageBoxButtons.OK, MessageBoxIcon.Information);
            // ponytail: a running exe cannot delete itself; a hidden cmd removes uninstall.exe and the empty folder 2 s
            // after this process exits. Started last and outside dir (the Start menu shortcut's working folder blocks rd).
            ProcessStartInfo psi = new ProcessStartInfo("cmd.exe",
                "/c ping -n 3 127.0.0.1 >nul & del /f /q \"" + Me.Location + "\" & rd \"" + dir + "\"");
            psi.CreateNoWindow = true;
            psi.UseShellExecute = false;
            psi.WorkingDirectory = Path.GetTempPath();
            Process.Start(psi);
            return 0;
        }
    }

    /// <summary>Four pages: welcome + license, options, progress, finish.</summary>
    class Wizard : Form
    {
        readonly Panel[] pages = new Panel[4];
        readonly Button back = new Button(), next = new Button(), cancel = new Button();
        readonly TextBox dirBox = new TextBox();
        readonly CheckBox desktopBox = new CheckBox(), audiverisBox = new CheckBox(), launchBox = new CheckBox();
        readonly ProgressBar bar = new ProgressBar();
        readonly Label status = new Label(), result = new Label();
        int page;
        bool busy;

        public Wizard()
        {
            Text = "SheetXML " + Setup.Version + " Setup";
            Font = SystemFonts.MessageBoxFont;
            AutoScaleMode = AutoScaleMode.Dpi;
            ClientSize = new Size(560, 400);
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }

            for (int i = 0; i < 4; i++)
            {
                pages[i] = new Panel { Bounds = new Rectangle(16, 12, 528, 330), Visible = false };
                Controls.Add(pages[i]);
            }
            // 0: welcome + license
            Add(0, Heading("Welcome to SheetXML " + Setup.Version), 0);
            Add(0, Para("SheetXML turns sheet music (PDF, scans, photos, MusicXML, ABC) into MusicXML with mandolin TAB. " +
                        "It runs on this PC in your web browser. Please read the license and notices below; " +
                        "clicking Next means you accept them."), 30, 56);
            TextBox lic = new TextBox { Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Both, WordWrap = false, Text = Setup.LicenseText(),
                                        Font = new Font(FontFamily.GenericMonospace, 8f), BackColor = SystemColors.Window, TabStop = false };
            Add(0, lic, 90, 236);
            // 1: options
            Add(1, Heading("Install options"), 0);
            Add(1, Para("Install folder (no administrator rights needed):"), 36, 20);
            dirBox.Text = Setup.DefaultDir;
            Add(1, dirBox, 58, 24).Width = 430;
            Button browse = new Button { Text = "Browse...", Bounds = new Rectangle(440, 57, 88, 26) };
            browse.Click += (s, e) =>
            {
                using (FolderBrowserDialog d = new FolderBrowserDialog { SelectedPath = dirBox.Text })
                    if (d.ShowDialog(this) == DialogResult.OK)
                        dirBox.Text = Path.GetFileName(d.SelectedPath.TrimEnd('\\')).Equals("SheetXML", StringComparison.OrdinalIgnoreCase)
                            ? d.SelectedPath : Path.Combine(d.SelectedPath, "SheetXML");
            };
            pages[1].Controls.Add(browse);
            desktopBox.Text = "Create a desktop shortcut";
            desktopBox.Checked = true;
            Add(1, desktopBox, 104, 24);
            bool hasAudiveris = File.Exists(Setup.AudiverisExe);
            audiverisBox.Text = hasAudiveris ? "Install Audiveris offline OMR (already installed)"
                                             : "Install Audiveris offline OMR (recommended, needs admin once)";
            audiverisBox.Checked = !hasAudiveris;
            Add(1, audiverisBox, 134, 24);
            Add(1, Para("Audiveris reads scans and photos without any AI service (free, AGPL-3.0, includes its own Java). " +
                        "Windows asks for administrator approval once. SheetXML also works without it: " +
                        "born-digital PDFs need nothing, scans can use an AI key."), 162, 60).Padding = new Padding(18, 0, 0, 0);
            Add(1, Para("Start menu shortcuts and an uninstaller (Windows Settings > Apps) are always created. " +
                        "Your API keys and transcriptions are stored in " + Setup.DataDir + "."), 236, 50);
            // 2: progress
            Add(2, Heading("Installing..."), 0);
            Add(2, bar, 60, 24).Width = 528;
            Add(2, status, 94, 40);
            // 3: finish
            Add(3, Heading("SheetXML is installed"), 0);
            Add(3, result, 40, 120);
            launchBox.Text = "Launch SheetXML now";
            launchBox.Checked = true;
            Add(3, launchBox, 170, 24);

            back.Text = "< Back"; next.Text = "Next >"; cancel.Text = "Cancel";
            back.Bounds = new Rectangle(280, 358, 84, 28);
            next.Bounds = new Rectangle(370, 358, 84, 28);
            cancel.Bounds = new Rectangle(460, 358, 84, 28);
            Controls.AddRange(new Control[] { back, next, cancel });
            back.Click += (s, e) => Go(page - 1);
            next.Click += (s, e) => Next();
            cancel.Click += (s, e) => Close();
            FormClosing += (s, e) => e.Cancel = busy;
            AcceptButton = next;
            Shown += (s, e) => { next.Focus(); lic.Select(0, 0); };
            Go(0);
        }

        static Label Heading(string t)
        {
            return new Label { Text = t, Font = new Font(SystemFonts.MessageBoxFont.FontFamily, 13f, FontStyle.Bold), AutoSize = true };
        }

        static Label Para(string t) { return new Label { Text = t, AutoSize = false }; }

        Control Add(int p, Control c, int top, int height = 30)
        {
            c.Bounds = new Rectangle(0, top, 528, height);
            pages[p].Controls.Add(c);
            return c;
        }

        void Go(int p)
        {
            page = p;
            for (int i = 0; i < 4; i++) pages[i].Visible = i == p;
            back.Enabled = p == 1;
            next.Text = p == 1 ? "Install" : p == 3 ? "Finish" : "Next >";
            next.Enabled = p != 2;
            cancel.Enabled = p < 2;
        }

        void Next()
        {
            if (page == 0) { Go(1); return; }
            if (page == 3)
            {
                if (launchBox.Checked)
                    try { Process.Start(new ProcessStartInfo(Path.Combine(dirBox.Text, "SheetXML.exe")) { WorkingDirectory = dirBox.Text }); }
                    catch (Exception e) { MessageBox.Show(e.Message, "SheetXML"); }
                Close();
                return;
            }
            string dir;
            try { dir = Path.GetFullPath(dirBox.Text.Trim()); }
            catch { MessageBox.Show("Please choose a valid install folder.", "SheetXML"); return; }
            dirBox.Text = dir;
            bool desktop = desktopBox.Checked, audiveris = audiverisBox.Checked;
            Go(2);
            busy = true;
            new Thread(() =>
            {
                string msg;
                bool ok = true;
                try
                {
                    msg = Setup.Install(dir, desktop, audiveris, (t, pct) => BeginInvoke((Action)(() =>
                    {
                        status.Text = t;
                        bar.Value = Math.Min(100, pct);
                    })));
                }
                catch (Exception e) { msg = "Installation failed: " + e.Message; ok = false; }
                Setup.Log(msg);
                BeginInvoke((Action)(() =>
                {
                    busy = false;
                    result.Text = msg.Replace(". ", ".\n\n") + (ok ? "\n\nStart it from the Start menu or the desktop shortcut." : "");
                    launchBox.Visible = launchBox.Checked = ok;
                    ((Label)pages[3].Controls[0]).Text = ok ? "SheetXML is installed" : "Setup did not finish";
                    Go(3);
                }));
            }) { IsBackground = true }.Start();
        }
    }
}
