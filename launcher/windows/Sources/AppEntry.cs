// SPDX-License-Identifier: MIT
// Copyright (C) 2026 Nicholas Vlamis

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using Microsoft.Win32;

namespace GammonView.Installer
{
    /// <summary>
    /// Where a Windows user looks for an app: Settings > Apps, and the Start menu.
    ///
    /// **Settings > Apps** lists whatever has a key under the per-user
    /// `Uninstall` branch, and its Uninstall button runs the command in it. That
    /// command needs a program that will still be there, and the copy somebody
    /// downloaded is in Downloads, where it will not be for long -- so the
    /// installer keeps a copy of itself in `bin`. The same copy is what the
    /// helper's menu opens to update (`gvhelper/winapp.py`), since nothing
    /// inside the helper can replace the files it is running from.
    ///
    /// **The Start menu shortcut** runs `pythonw.exe -m gvhelper start`, so what
    /// opening it does is decided in the package, which updates, rather than
    /// here, which does not. It is the way back after Quit, as the app in
    /// Applications is on macOS.
    ///
    /// Both are per-user and need no administrator rights, like everything else
    /// the installer makes. Both are written on every install and update, and
    /// neither failing fails the install: the helper runs without them, and the
    /// installer itself can always uninstall.
    /// </summary>
    public static class AppEntry
    {
        public const string DisplayName = "GammonView Helper";

        /// <summary>Under `HKEY_CURRENT_USER`.</summary>
        public const string UninstallKey =
            @"Software\Microsoft\Windows\CurrentVersion\Uninstall\GammonViewHelper";

        public static string Shortcut => Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.Programs), DisplayName + ".lnk");

        /// <summary>The arguments the shortcut gives `pythonw.exe`.</summary>
        public static readonly string[] StartArguments = { "-m", "gvhelper", "start" };

        /// <summary>
        /// The values of the Settings entry. Pure, so the checks can read them.
        ///
        /// `NoModify` and `NoRepair` because there is nothing to modify, and a
        /// repair is an update: offering both would be two buttons that do what
        /// a third already does. The version is the helper's, which is what the
        /// person can do something about; the installer's own is in its file.
        /// </summary>
        public static Dictionary<string, object> Values(string installer, string helperVersion,
            string root, int sizeKb, DateTime today)
        {
            return new Dictionary<string, object>
            {
                ["DisplayName"] = DisplayName,
                ["DisplayVersion"] = helperVersion,
                ["Publisher"] = "GammonView",
                ["DisplayIcon"] = installer + ",0",
                ["UninstallString"] = Shell.Quote(installer) + " --uninstall",
                ["InstallLocation"] = root,
                ["URLInfoAbout"] = "https://gammonview.com",
                ["HelpLink"] = "https://gammonview.com/#/info/helper",
                ["InstallDate"] = today.ToString("yyyyMMdd"),
                ["EstimatedSize"] = sizeKb,
                ["NoModify"] = 1,
                ["NoRepair"] = 1,
            };
        }

        /// <summary>
        /// Keep a copy of this program in `bin`, unless that copy is newer.
        ///
        /// Not when it *is* that copy (an update from the menu), and not over a
        /// newer one: an old download opened again would otherwise swap the
        /// uninstaller and updater for an older one. Done in a sandboxed run as
        /// well, since it touches only the root.
        /// </summary>
        public static void PlaceSelf()
        {
            var self = Path.GetFullPath(Assembly.GetExecutingAssembly().Location);
            var copy = Path.GetFullPath(Paths.Installer);
            if (string.Equals(self, copy, StringComparison.OrdinalIgnoreCase)) return;
            if (File.Exists(copy))
            {
                try
                {
                    var theirs = AssemblyName.GetAssemblyName(copy).Version;
                    var mine = Assembly.GetExecutingAssembly().GetName().Version;
                    if (theirs != null && mine != null && theirs > mine) return;
                }
                catch { /* unreadable: replace it */ }
            }
            File.Copy(self, copy, overwrite: true);
        }

        public static void Install(string helperVersion)
        {
            try { PlaceSelf(); } catch { return; }  // nothing below works without it
            if (Paths.Sandboxed != null) return;
            try { WriteUninstallEntry(helperVersion); } catch { }
            try { WriteShortcut(); } catch { }
        }

        /// <summary>Remove both. Throws nothing.</summary>
        public static void Remove()
        {
            if (Paths.Sandboxed != null) return;
            try { Registry.CurrentUser.DeleteSubKeyTree(UninstallKey, throwOnMissingSubKey: false); }
            catch { }
            try { if (File.Exists(Shortcut)) File.Delete(Shortcut); } catch { }
        }

        private static void WriteUninstallEntry(string helperVersion)
        {
            var values = Values(Paths.Installer, helperVersion, Paths.Root, SizeKb(Paths.Root),
                DateTime.Now);
            using (var key = Registry.CurrentUser.CreateSubKey(UninstallKey))
            {
                if (key == null) return;
                foreach (var pair in values)
                {
                    if (pair.Value is int n) key.SetValue(pair.Key, n, RegistryValueKind.DWord);
                    else key.SetValue(pair.Key, (string)pair.Value, RegistryValueKind.String);
                }
            }
        }

        /// <summary>
        /// The size Settings shows. Counted rather than guessed, since it is
        /// mostly the engine and its Python, and those move with releases.
        /// </summary>
        private static int SizeKb(string root)
        {
            long bytes = 0;
            try
            {
                foreach (var f in new DirectoryInfo(root).EnumerateFiles("*", SearchOption.AllDirectories))
                {
                    try { bytes += f.Length; } catch { }
                }
            }
            catch { }
            return (int)Math.Min(int.MaxValue, bytes / 1024);
        }

        /// <summary>
        /// Through `WScript.Shell`, which every Windows has: the `IShellLink`
        /// interop it wraps is forty lines of declarations to set five fields.
        /// </summary>
        private static void WriteShortcut()
        {
            var type = Type.GetTypeFromProgID("WScript.Shell");
            if (type == null) return;
            object? shell = null, link = null;
            try
            {
                shell = Activator.CreateInstance(type);
                link = type.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell,
                    new object[] { Shortcut });
                if (link == null) return;
                var t = link.GetType();
                void Set(string name, string value) =>
                    t.InvokeMember(name, BindingFlags.SetProperty, null, link, new object[] { value });
                Set("TargetPath", Paths.Pythonw);
                Set("Arguments", Shell.CommandLine(StartArguments));
                Set("WorkingDirectory", Paths.Root);
                Set("IconLocation", Paths.Installer + ",0");
                Set("Description", "Start GammonView Helper, which analyses your matches on this computer.");
                t.InvokeMember("Save", BindingFlags.InvokeMethod, null, link, null);
            }
            finally
            {
                if (link != null && Marshal.IsComObject(link)) Marshal.FinalReleaseComObject(link);
                if (shell != null && Marshal.IsComObject(shell)) Marshal.FinalReleaseComObject(shell);
            }
        }

        // MARK: - uninstalling from Settings

        /// <summary>True when this process is the copy in `bin`.</summary>
        public static bool RunningFromRoot()
        {
            var self = Path.GetFullPath(Assembly.GetExecutingAssembly().Location);
            var root = Path.GetFullPath(Paths.Root).TrimEnd('\\') + "\\";
            return self.StartsWith(root, StringComparison.OrdinalIgnoreCase);
        }

        /// <summary>
        /// Run the uninstall from a copy in `%TEMP%`, and say whether one started.
        ///
        /// Settings starts the copy in `bin`, and the uninstall deletes `bin`:
        /// Windows will not delete a program that is running, so the copy in
        /// `bin` hands over to one outside the root and exits. `--temporary`
        /// tells that one to delete itself when it closes.
        /// </summary>
        public static bool HandOffUninstall()
        {
            try
            {
                var temp = Path.Combine(Path.GetTempPath(),
                    "GammonView-Uninstall-" + Guid.NewGuid().ToString("N").Substring(0, 8) + ".exe");
                File.Copy(Assembly.GetExecutingAssembly().Location, temp);
                Process.Start(new ProcessStartInfo(temp, "--uninstall --temporary")
                {
                    UseShellExecute = false,
                    WorkingDirectory = Path.GetTempPath(),
                })?.Dispose();
                return true;
            }
            catch { return false; }
        }

        /// <summary>
        /// Delete this program once it has exited: a hidden `cmd` that waits two
        /// seconds, then deletes it. `%TEMP%` is cleaned eventually anyway; this
        /// is so that "eventually" is not "never" on a machine nobody cleans.
        /// </summary>
        public static void DeleteSelfLater()
        {
            try
            {
                var self = Assembly.GetExecutingAssembly().Location;
                Process.Start(new ProcessStartInfo("cmd.exe",
                    // Quoted for cmd, not by `Shell.Quote`'s rules: what
                    // matters to cmd is an `&` in a user name, not a space.
                    "/d /c ping -n 3 127.0.0.1 >nul & del /f /q \"" + self + "\"")
                {
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    WorkingDirectory = Path.GetTempPath(),
                })?.Dispose();
            }
            catch { }
        }
    }
}
