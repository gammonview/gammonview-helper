// SPDX-License-Identifier: MIT
// Copyright (C) 2026 Nicholas Vlamis

using System;
using System.Collections.Generic;
using System.IO;
using System.Security;
using System.Security.Principal;
using System.Text;
using System.Xml;

namespace GammonView.Installer
{
    /// <summary>
    /// Registering the helper so it is there after a reboot: a scheduled task.
    ///
    /// **Why a task and not the `Run` registry key**, which is the simpler and
    /// more familiar answer. `Run` starts a program once at sign-in and then
    /// forgets it, which is launchd without `KeepAlive`: a helper that crashes
    /// at 2am stays down until the next sign-in, and nothing on screen says so.
    /// A task can restart on failure, can be stopped and started by name --
    /// which the update needs, because Windows will not replace files a running
    /// process holds (see `Shell.StopEverythingUnder`) -- and needs no
    /// administrator rights when it runs as the user who created it.
    ///
    /// The decisions that mirror the macOS plist, and the ones that differ:
    ///
    /// * **`pythonw.exe -m gvhelper`**, never the console entry point. See
    ///   `Paths.Pythonw`.
    /// * **`--site` on the command line**, where macOS sets `GAMMONVIEW_SITE`.
    ///   A task has no environment of its own to set, and the flag outranks
    ///   `config.json` exactly as the variable does. It is still only ever the
    ///   manifest's answer, and a manifest naming no site writes no flag.
    /// * **Priority 6, which is Normal**, not the task default of 7. The helper
    ///   lowers its own priority (`runner.lower_priority`), and the default also
    ///   drops the task's I/O and memory priority -- the Windows shape of the
    ///   `ProcessType=Background` measurement the macOS plist records.
    /// * **Batteries do not stop it.** The task defaults are "do not start on
    ///   battery" and "stop when unplugged", which on a laptop would make the
    ///   helper vanish whenever somebody walks away from the desk, silently.
    /// * **No time limit.** The default `ExecutionTimeLimit` is three days,
    ///   after which Windows ends the task -- a helper that dies every Thursday.
    /// </summary>
    public static class LoginItem
    {
        /// <summary>
        /// The task's name, carrying the user's SID.
        ///
        /// Tasks live in one machine-wide folder. A family PC with two people
        /// each running the installer would otherwise have the second one fail
        /// to overwrite the first one's task -- or, worse, replace it.
        /// </summary>
        public static string TaskName(string sid) => "GammonView Helper " + sid;

        private static WindowsIdentity Me => WindowsIdentity.GetCurrent();

        private static string CurrentTaskName => TaskName(Me.User!.Value);

        /// <summary>The action's arguments: global options before the subcommand.</summary>
        public static List<string> Arguments(string? site)
        {
            var args = new List<string> { "-m", "gvhelper" };
            if (!string.IsNullOrEmpty(site)) { args.Add("--site"); args.Add(site!); }
            args.Add("run");
            return args;
        }

        /// <summary>The task definition. Pure, so it can be checked without installing one.</summary>
        public static string Document(string pythonw, string workingDirectory, string? site,
            string userName, string sid)
        {
            string X(string s) => SecurityElement.Escape(s)!;
            return
$@"<?xml version=""1.0"" encoding=""UTF-16""?>
<Task version=""1.2"" xmlns=""http://schemas.microsoft.com/windows/2004/02/mit/task"">
  <RegistrationInfo>
    <Author>GammonView</Author>
    <Description>Analyses GammonView matches on this computer. To remove it, uninstall GammonView Helper in Settings, Apps.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{X(userName)}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id=""Author"">
      <UserId>{X(sid)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>6</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>999</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context=""Author"">
    <Exec>
      <Command>{X(pythonw)}</Command>
      <Arguments>{X(Shell.CommandLine(Arguments(site)))}</Arguments>
      <WorkingDirectory>{X(workingDirectory)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
";
        }

        private static void Schtasks(string what, bool check, params string[] args) =>
            Shell.Run("schtasks.exe", args, what, check: check);

        /// <summary>
        /// Install and start, replacing whatever was there.
        ///
        /// Idempotent, because it runs on every update as well as the first
        /// install. Expects the old helper to be stopped already (`Stop`), since
        /// the files it runs have just been replaced.
        /// </summary>
        public static void Install(string? site)
        {
            // Checked before the sandbox returns, so a test install checks it too:
            // it is the one file this step depends on that `uv` does not promise.
            if (!File.Exists(Paths.Pythonw))
                throw new ShellException(
                    "The helper installed, but without the program that runs it in the "
                    + "background (pythonw.exe). Try again; if it happens twice, tell us.");
            if (Paths.Sandboxed != null) return;

            var me = Me;
            var name = TaskName(me.User!.Value);
            var xml = Document(Paths.Pythonw, Paths.Root, site, me.Name, me.User.Value);

            // UTF-16 with a byte-order mark, because the document says so and
            // `schtasks /XML` believes the declaration over the bytes.
            var file = Path.Combine(Paths.Root, "login-item.xml");
            File.WriteAllText(file, xml, Encoding.Unicode);
            try
            {
                Schtasks("Registering GammonView Helper", true,
                    "/Create", "/TN", name, "/XML", file, "/F");
            }
            finally { try { File.Delete(file); } catch { } }

            Schtasks("Starting GammonView Helper", true, "/Run", "/TN", name);
        }

        /// <summary>
        /// Stop the helper, wherever it was started from. Throws nothing.
        ///
        /// Ending the task first lets Task Scheduler record it as stopped rather
        /// than failed, which is what keeps `RestartOnFailure` from starting it
        /// again in the middle of the update that stopped it.
        /// </summary>
        public static void Stop()
        {
            if (Paths.Sandboxed == null)
            {
                try { Schtasks("Stopping GammonView Helper", false, "/End", "/TN", CurrentTaskName); }
                catch { /* no task yet is the first install, not a failure */ }
            }
            Shell.StopEverythingUnder(Paths.Root);
        }

        /// <summary>`Install`'s inverse, and like it on macOS, throws nothing.</summary>
        public static void Remove()
        {
            Stop();
            if (Paths.Sandboxed != null) return;
            try { Schtasks("Removing GammonView Helper", false, "/Delete", "/TN", CurrentTaskName, "/F"); }
            catch { }
        }

        /// <summary>
        /// Which site the installed helper has actually been polling, if it says.
        ///
        /// Read back out of the task this program registered, for the reason the
        /// macOS launcher reads its plist: `config.json` is the one record that
        /// cannot be trusted here, and this one needs no network.
        /// </summary>
        public static string? InstalledSite()
        {
            if (Paths.Sandboxed != null) return null;
            var xml = new StringBuilder();
            try
            {
                var code = Shell.Run("schtasks.exe",
                    new[] { "/Query", "/TN", CurrentTaskName, "/XML" },
                    "Reading the login item", onOutput: line => xml.AppendLine(line), check: false);
                if (code != 0) return null;
            }
            catch { return null; }
            return SiteFromDocument(xml.ToString());
        }

        /// <summary>The parsing half of `InstalledSite`, testable against `Document`.</summary>
        public static string? SiteFromDocument(string? xml)
        {
            if (string.IsNullOrEmpty(xml)) return null;
            string? arguments;
            try
            {
                var doc = new XmlDocument();
                doc.LoadXml(xml);
                var ns = new XmlNamespaceManager(doc.NameTable);
                ns.AddNamespace("t", "http://schemas.microsoft.com/windows/2004/02/mit/task");
                arguments = doc.SelectSingleNode("/t:Task/t:Actions/t:Exec/t:Arguments", ns)?.InnerText;
            }
            catch (XmlException) { return null; }
            if (arguments == null) return null;

            var argv = SplitCommandLine(arguments);
            var at = argv.IndexOf("--site");
            if (at < 0 || at + 1 >= argv.Count) return null;
            var site = argv[at + 1];
            return site.Length == 0 ? null : site;
        }

        /// <summary>
        /// `Shell.Quote`'s inverse, enough for what `Document` writes. Pure C#
        /// rather than `CommandLineToArgvW` so the checks run off Windows too.
        /// </summary>
        public static List<string> SplitCommandLine(string line)
        {
            var result = new List<string>();
            var current = new StringBuilder();
            var inQuotes = false;
            var any = false;
            var i = 0;
            while (i < line.Length)
            {
                var c = line[i];
                if (c == '\\')
                {
                    var n = 0;
                    while (i < line.Length && line[i] == '\\') { n++; i++; }
                    if (i < line.Length && line[i] == '"')
                    {
                        current.Append('\\', n / 2);
                        if (n % 2 == 1) { current.Append('"'); i++; }
                    }
                    else current.Append('\\', n);
                    any = true;
                    continue;
                }
                if (c == '"') { inQuotes = !inQuotes; any = true; i++; continue; }
                if ((c == ' ' || c == '\t') && !inQuotes)
                {
                    if (any) { result.Add(current.ToString()); current.Clear(); any = false; }
                    i++;
                    continue;
                }
                current.Append(c);
                any = true;
                i++;
            }
            if (any) result.Add(current.ToString());
            return result;
        }
    }
}
