// SPDX-License-Identifier: MIT
// Copyright (C) 2026 Nicholas Vlamis

using System;
using System.Collections.Generic;
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
    /// command is the helper's own `uninstall` (`gvhelper/winmaint.py`), run by
    /// `pythonw.exe` from the venv -- **not a copy of this program**. An
    /// installer that kept a copy of itself in the root for that, and for the
    /// menu's Update, was what Windows Defender quarantined as
    /// `Behavior:Win32/Persistence.A!ml`: an unsigned program copying itself
    /// into AppData and registering the copy is what malware does too. So this
    /// program leaves nothing of itself behind but its icon, written out as a
    /// file for the entry and the shortcut to show.
    ///
    /// **The Start menu shortcut** runs `pythonw.exe -m gvhelper start`, so what
    /// opening it does is decided in the package, which updates, rather than
    /// here, which does not. It is the way back after Quit, as the app in
    /// Applications is on macOS.
    ///
    /// Both are per-user and need no administrator rights, like everything else
    /// the installer makes. Both are written on every install and update, and
    /// neither failing fails the install: the helper runs without them, and
    /// opening the installer again can always uninstall.
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
        /// What Settings' Uninstall gives `pythonw.exe`: `--site` as the task
        /// has it (`LoginItem.Arguments`), so the unlink reaches the right site.
        /// </summary>
        public static List<string> UninstallArguments(string? site)
        {
            var args = new List<string> { "-m", "gvhelper" };
            if (!string.IsNullOrEmpty(site)) { args.Add("--site"); args.Add(site!); }
            args.Add("uninstall");
            return args;
        }

        /// <summary>The name `GammonViewInstaller.csproj` embeds the icon under.</summary>
        public const string IconResource = "icon.ico";

        /// <summary>
        /// The values of the Settings entry. Pure, so the checks can read them.
        ///
        /// `NoModify` and `NoRepair` because there is nothing to modify, and a
        /// repair is an update: offering both would be two buttons that do what
        /// a third already does. The version is the helper's, which is what the
        /// person can do something about; the installer's own is in its file.
        /// </summary>
        public static Dictionary<string, object> Values(string pythonw, string icon, string? site,
            string helperVersion, string root, int sizeKb, DateTime today)
        {
            return new Dictionary<string, object>
            {
                ["DisplayName"] = DisplayName,
                ["DisplayVersion"] = helperVersion,
                ["Publisher"] = "GammonView",
                ["DisplayIcon"] = icon,
                ["UninstallString"] = Shell.Quote(pythonw) + " " + Shell.CommandLine(UninstallArguments(site)),
                ["InstallLocation"] = root,
                ["URLInfoAbout"] = "https://gammonview.com",
                ["HelpLink"] = "https://gammonview.com/#/info/helper",
                ["InstallDate"] = today.ToString("yyyyMMdd"),
                ["EstimatedSize"] = sizeKb,
                ["NoModify"] = 1,
                ["NoRepair"] = 1,
            };
        }

        public static void Install(string helperVersion, string? site)
        {
            if (Paths.Sandboxed != null) return;
            try { WriteIcon(); } catch { }
            try { WriteUninstallEntry(helperVersion, site); } catch { }
            try { WriteShortcut(); } catch { }
        }

        private static void WriteIcon()
        {
            using (var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream(IconResource))
            {
                if (stream == null) return;
                using (var file = File.Create(Paths.Icon)) stream.CopyTo(file);
            }
        }

        /// <summary>Remove both. Throws nothing.</summary>
        public static void Remove()
        {
            if (Paths.Sandboxed != null) return;
            try { Registry.CurrentUser.DeleteSubKeyTree(UninstallKey, throwOnMissingSubKey: false); }
            catch { }
            try { if (File.Exists(Shortcut)) File.Delete(Shortcut); } catch { }
        }

        private static void WriteUninstallEntry(string helperVersion, string? site)
        {
            var values = Values(Paths.Pythonw, Paths.Icon, site, helperVersion, Paths.Root,
                SizeKb(Paths.Root), DateTime.Now);
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
                Set("IconLocation", Paths.Icon);
                Set("Description", "Start GammonView Helper, which analyses your matches on this computer.");
                t.InvokeMember("Save", BindingFlags.InvokeMethod, null, link, null);
            }
            finally
            {
                if (link != null && Marshal.IsComObject(link)) Marshal.FinalReleaseComObject(link);
                if (shell != null && Marshal.IsComObject(shell)) Marshal.FinalReleaseComObject(shell);
            }
        }
    }
}
