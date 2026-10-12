// SPDX-License-Identifier: MIT
// Copyright (C) 2026 Nicholas Vlamis

using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Reflection;

namespace GammonView.Installer
{
    /// <summary>Everything the window is asked to show. See `InstallerEvent` on macOS.</summary>
    public abstract class InstallerEvent
    {
        public sealed class Step : InstallerEvent
        {
            public string Title { get; }
            public string Detail { get; }
            public Step(string title, string detail) { Title = title; Detail = detail; }
        }

        public sealed class Notice : InstallerEvent
        {
            public string Text { get; }
            public Notice(string text) { Text = text; }
        }

        public sealed class Word : InstallerEvent
        {
            public string Text { get; }
            public string Url { get; }
            public Word(string text, string url) { Text = text; Url = url; }
        }

        /// <summary>An install is already here: update it, or take it off this computer.</summary>
        public sealed class Choice : InstallerEvent
        {
            public string Message { get; }
            public Choice(string message) { Message = message; }
        }

        public sealed class Finished : InstallerEvent
        {
            public string Title { get; }
            public string Message { get; }
            public Finished(string title, string message) { Title = title; Message = message; }
        }

        public sealed class Failed : InstallerEvent
        {
            public string Message { get; }
            public bool Retry { get; }
            public bool DownloadPage { get; }
            public Failed(string message, bool retry, bool downloadPage)
            {
                Message = message; Retry = retry; DownloadPage = downloadPage;
            }
        }
    }

    /// <summary>What became of the row in the user's account settings.</summary>
    public enum AccountOutcomeKind { Removed, Left, Skipped }

    public sealed class AccountOutcome
    {
        public AccountOutcomeKind Kind { get; }
        public string Reason { get; }
        public AccountOutcome(AccountOutcomeKind kind, string reason = "") { Kind = kind; Reason = reason; }
    }

    /// <summary>
    /// The install, as a sequence of steps that genuinely depend on each other.
    ///
    /// A port of `Installer.swift`, step for step, and the comments there are
    /// the reasoning for every step here; what is written below is only where
    /// Windows made a step different. The two that matter:
    ///
    /// * **The running helper is stopped before anything is replaced.** Windows
    ///   will not overwrite a loaded DLL. See `Shell.StopEverythingUnder`.
    /// * **`uv.exe` travels inside this program** as an embedded resource, so
    ///   the download is one `.exe` rather than a zip to extract first -- the
    ///   step most likely to lose somebody on Windows, where running a program
    ///   from inside a zip "works" from a temporary folder that then vanishes.
    /// </summary>
    public static class Installer
    {
        public static bool IsInstalled => File.Exists(Paths.Helper);

        public static void Start(Action<InstallerEvent> emit)
        {
            if (!IsInstalled) { Install(emit); return; }
            emit(new InstallerEvent.Choice(
                "GammonView Helper is already installed on this computer.\n\n"
                + "Update it to the current version, or remove it completely."));
        }

        public static void Install(Action<InstallerEvent> emit) => Install(emit, pair: true);

        /// <param name="pair">
        /// False only in a headless smoke run (`Program.Headless`), which stops
        /// where the device flow would start: there is nobody to confirm a word.
        /// </param>
        public static void Install(Action<InstallerEvent> emit, bool pair)
        {
            try
            {
                emit(new InstallerEvent.Step("Getting ready", "Checking which version to install…"));
                var manifest = Manifest.Fetch();
                if (!string.IsNullOrEmpty(manifest.Notice)) emit(new InstallerEvent.Notice(manifest.Notice!));

                emit(new InstallerEvent.Step("Getting ready", "Making room…"));
                Paths.Create();
                // Before uv is touched, and before the venv is: both may be
                // running right now, as the helper and as its interpreter.
                LoginItem.Stop();
                PlaceUv();

                emit(new InstallerEvent.Step("Installing",
                    "Downloading Python and the analysis engine. "
                    + "This is about 80 MB and can take a few minutes."));
                InstallHelper(manifest, line => emit(new InstallerEvent.Step("Installing", line)));

                emit(new InstallerEvent.Step("Almost there", "Starting GammonView Helper…"));
                LoginItem.Install(manifest.Site);

                var linked = IsLinked(manifest);
                if (!pair)
                {
                    emit(new InstallerEvent.Finished("Installed",
                        "gammonview-helper " + manifest.Version + " is installed; linked: " + linked));
                    return;
                }
                if (linked)
                {
                    emit(new InstallerEvent.Finished("All set",
                        "GammonView Helper is up to date and linked to your account.\n\n"
                        + "It runs in the background. There is nothing else to do."));
                    return;
                }
                Pair(manifest, emit);
            }
            catch (ManifestException e)
            {
                var tooOld = e.Reason == ManifestException.Kind.LauncherTooOld;
                emit(new InstallerEvent.Failed(e.Message, retry: !tooOld, downloadPage: tooOld));
            }
            catch (Exception e)
            {
                emit(new InstallerEvent.Failed(e.Message, retry: true, downloadPage: false));
            }
        }

        // MARK: - uninstall

        /// <summary>
        /// Take the helper off this computer, and off the account. Same order as
        /// macOS, for the same forced reasons; nothing here throws.
        /// </summary>
        public static void Uninstall(Action<InstallerEvent> emit)
        {
            emit(new InstallerEvent.Step("Removing", "Unlinking this computer from your account…"));
            var account = UnlinkFromAccount();

            emit(new InstallerEvent.Step("Removing", "Stopping GammonView Helper…"));
            LoginItem.Remove();

            emit(new InstallerEvent.Step("Removing", "Deleting files…"));
            Shell.DeleteTree(Paths.Root);

            string closing;
            switch (account.Kind)
            {
                case AccountOutcomeKind.Removed:
                    closing = "It has also been removed from your account settings.";
                    break;
                case AccountOutcomeKind.Left:
                    closing = "It could not be removed from your account settings (" + account.Reason
                        + "), so it may still be listed there. Use Unlink beside it on gammonview.com.";
                    break;
                default:
                    closing = "It may still be listed in your account settings. Use Unlink "
                        + "beside it on gammonview.com.";
                    break;
            }
            var leftovers = Directory.Exists(Paths.Root)
                ? "\n\nSome files could not be deleted. Restart the computer and run this "
                  + "again, or delete this folder:\n" + Paths.Root
                : "";
            emit(new InstallerEvent.Finished("Removed",
                "GammonView Helper is no longer on this computer.\n\n" + closing + leftovers
                + "\n\nYou can install it again any time from gammonview.com."));
        }

        private static AccountOutcome UnlinkFromAccount()
        {
            if (!IsInstalled) return new AccountOutcome(AccountOutcomeKind.Skipped);
            var site = LoginItem.InstalledSite();

            string? line = null;
            try
            {
                Shell.Run(Paths.Helper, UnlinkArguments(porcelain: true, site: site),
                    "Unlinking this computer", HelperEnvironment, onOutput: l => line = l);
                return AccountVerdict(line);
            }
            catch { /* fall through and try again without the flag -- see Installer.swift */ }

            try
            {
                Shell.Run(Paths.Helper, UnlinkArguments(porcelain: false, site: site),
                    "Unlinking this computer", HelperEnvironment);
            }
            catch { }
            return new AccountOutcome(AccountOutcomeKind.Skipped);
        }

        public static List<string> UnlinkArguments(bool porcelain, string? site)
        {
            var args = new List<string>();
            if (!string.IsNullOrEmpty(site)) { args.Add("--site"); args.Add(site!); }
            args.Add("unlink");
            if (porcelain) args.Add("--porcelain");
            return args;
        }

        public static AccountOutcome AccountVerdict(string? line)
        {
            var o = Json.Object(line);
            if (o == null) return new AccountOutcome(AccountOutcomeKind.Skipped);
            switch (Json.String(o, "account"))
            {
                case "removed":
                    return new AccountOutcome(AccountOutcomeKind.Removed);
                case "left":
                    return new AccountOutcome(AccountOutcomeKind.Left,
                        Json.String(o, "reason") ?? "could not reach gammonview.com");
                default:
                    return new AccountOutcome(AccountOutcomeKind.Skipped);
            }
        }

        // MARK: - uv

        /// <summary>The name `build.sh` embeds the uv zip under.</summary>
        public const string UvResource = "uv.zip";

        /// <summary>
        /// The `uv.exe` that came inside this program, written into the install root.
        ///
        /// Written on every run, for the reason the macOS launcher copies on
        /// every run: a newer installer carrying a newer uv must not keep using
        /// whatever the first one left behind.
        /// </summary>
        private static void PlaceUv()
        {
            var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream(UvResource);
            if (stream == null)
                throw new ShellException("This copy of the installer is incomplete. Download it again.");
            using (stream)
            using (var zip = new ZipArchive(stream, ZipArchiveMode.Read))
            {
                ZipArchiveEntry? entry = null;
                foreach (var e in zip.Entries)
                    if (string.Equals(e.Name, "uv.exe", StringComparison.OrdinalIgnoreCase)) entry = e;
                if (entry == null)
                    throw new ShellException("This copy of the installer is incomplete. Download it again.");
                if (File.Exists(Paths.Uv)) File.Delete(Paths.Uv);
                entry.ExtractToFile(Paths.Uv);
            }
        }

        /// <summary>
        /// Where `uv` is told to put everything. The macOS list, unchanged; see
        /// `uvEnvironment` there for why each one is set.
        /// </summary>
        private static Dictionary<string, string> UvEnvironment => new Dictionary<string, string>
        {
            ["UV_TOOL_DIR"] = Paths.Tools,
            ["UV_TOOL_BIN_DIR"] = Paths.Bin,
            ["UV_CACHE_DIR"] = Paths.Cache,
            ["UV_PYTHON_INSTALL_DIR"] = Paths.Python,
            ["UV_PYTHON_PREFERENCE"] = "only-managed",
            ["UV_NO_MODIFY_PATH"] = "1",
        };

        /// <summary>
        /// The environment every helper invocation gets.
        ///
        /// `PYTHONUTF8` because Python writes to a pipe on Windows in the ANSI
        /// code page, and the porcelain lines this program parses are UTF-8 by
        /// contract. `GAMMONVIEW_CONFIG_DIR` only in a sandboxed run, so a test
        /// install does not read and write the real helper's `config.json`.
        /// </summary>
        private static Dictionary<string, string> HelperEnvironment
        {
            get
            {
                var env = new Dictionary<string, string> { ["PYTHONUTF8"] = "1" };
                if (Paths.Sandboxed != null) env["GAMMONVIEW_CONFIG_DIR"] = Paths.Root;
                return env;
            }
        }

        private static void InstallHelper(Manifest manifest, Action<string> onLine)
        {
            Shell.Run(Paths.Uv, InstallArguments(manifest), "Installing GammonView Helper",
                UvEnvironment,
                onError: line =>
                {
                    var text = Shell.Plain(line);
                    if (text.Length > 0) onLine(text);
                });
        }

        public static List<string> InstallArguments(Manifest manifest)
        {
            var args = new List<string> { "tool", "install", "--force" };
            if (!string.IsNullOrEmpty(manifest.Python)) { args.Add("--python"); args.Add(manifest.Python!); }
            args.Add("gammonview-helper==" + manifest.Version);
            return args;
        }

        // MARK: - the helper's own answers

        public static List<string> GlobalArguments(Manifest manifest)
        {
            var args = new List<string>();
            if (!string.IsNullOrEmpty(manifest.Site)) { args.Add("--site"); args.Add(manifest.Site!); }
            return args;
        }

        private static bool IsLinked(Manifest manifest)
        {
            string? line = null;
            var args = GlobalArguments(manifest);
            args.Add("status");
            args.Add("--porcelain");
            try
            {
                // Exit 1 means "not linked", and said so on stdout.
                Shell.Run(Paths.Helper, args, "Checking this computer", HelperEnvironment,
                    onOutput: l => line = l, check: false);
            }
            catch { }
            return LinkedVerdict(line);
        }

        /// <summary>
        /// May the finished screen claim this machine is set up? Only `link ==
        /// "working"` says so; see `linkedVerdict` on macOS for why `linked` is
        /// the weaker field and is read only when `link` is absent.
        /// </summary>
        public static bool LinkedVerdict(string? line)
        {
            var o = Json.Object(line);
            if (o == null) return false;
            if (o.ContainsKey("link")) return Json.String(o, "link") == "working";
            return Json.Bool(o, "linked") ?? false;
        }

        private static void Pair(Manifest manifest, Action<InstallerEvent> emit)
        {
            InstallerEvent? terminal = null;
            var args = GlobalArguments(manifest);
            args.AddRange(new[] { "link", "--porcelain", "--no-browser" });
            try
            {
                Shell.Run(Paths.Helper, args, "Linking this computer", HelperEnvironment,
                    onOutput: line =>
                    {
                        var ev = PairingEvent(line);
                        if (ev is InstallerEvent.Word) emit(ev);
                        else if (ev != null) terminal = ev;
                    },
                    check: false);
            }
            catch { }

            emit(terminal ?? new InstallerEvent.Failed(
                "Linking stopped unexpectedly.", retry: true, downloadPage: false));
        }

        /// <summary>One line of `link --porcelain`, as the window should show it.</summary>
        public static InstallerEvent? PairingEvent(string line)
        {
            var o = Json.Object(line);
            if (o == null) return null;
            switch (Json.String(o, "event"))
            {
                case "offer":
                    var word = Json.String(o, "word");
                    var url = Json.String(o, "url");
                    // Web addresses only. The window hands this to the shell to
                    // open, and on Windows the shell opens a path by running it.
                    if (word == null || url == null || !Uri.TryCreate(url, UriKind.Absolute, out var u)
                        || (u.Scheme != "https" && u.Scheme != "http"))
                        return null;
                    return new InstallerEvent.Word(word, url);
                case "linked":
                    return new InstallerEvent.Finished("All set",
                        "This computer is linked.\n\n"
                        + "GammonView Helper runs in the background from now on. "
                        + "Choose it on gammonview.com when you analyse a match.");
                case "failed":
                    return new InstallerEvent.Failed(
                        Json.String(o, "reason") ?? "Linking did not finish.",
                        retry: Json.Bool(o, "retry") ?? true, downloadPage: false);
                default:
                    return null;  // a field we do not know yet is not an error
            }
        }
    }
}
