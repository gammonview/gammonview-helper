// SPDX-License-Identifier: MIT
// Copyright (C) 2026 Nicholas Vlamis

//
// The GammonView installer for Windows.
//
// It finds `uv`, installs `gammonview-helper` from PyPI into a private
// directory, registers a login item, and hands pairing to the package it just
// installed. Then it stops. The same specification as the macOS launcher, and
// the same reason for its shortness: this program ships **unsigned**, so a
// person gets past SmartScreen exactly once, and anything that changes must
// live on the other side of PyPI where updating it costs nobody a dialog.
//
// See `docs/DesktopHelper.md` in the GammonView repository, "The native
// surface is a launcher, and it is frozen", and `launcher/README.md` here.
//

using System;
using System.Threading;
using System.Windows.Forms;

namespace GammonView.Installer
{
    internal static class Program
    {
        /// <summary>Which sequence Try Again would run. See `AppDelegate.action` on macOS.</summary>
        private static Action<Action<InstallerEvent>> action = Installer.Start;

        private static InstallerWindow? window;

        [STAThread]
        private static int Main()
        {
            if (Paths.Sandboxed != null
                && Environment.GetEnvironmentVariable("GAMMONVIEW_INSTALLER_HEADLESS") == "1")
                return Headless();

            // One at a time. Two copies would run two `uv tool install`s into one
            // directory, and on Windows the second double-click is common: the
            // first one is slow to show a window while SmartScreen inspects it.
            using (var mutex = new Mutex(true, @"Local\GammonViewInstaller", out var first))
            {
                if (!first)
                {
                    MessageBox.Show("The GammonView installer is already open.", "GammonView Helper",
                        MessageBoxButtons.OK, MessageBoxIcon.Information);
                    return 1;
                }

                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);

                window = new InstallerWindow();
                window.OnRetry = () => Run();
                window.OnUpdate = () => Run(Installer.Install);
                window.OnUninstall = () => Run(Installer.Uninstall);
                window.Shown += (s, e) =>
                {
                    window.Render(new InstallerEvent.Step("Getting ready", "Starting…"));
                    Run();
                };
                Application.Run(window);
                return 0;
            }
        }

        /// <summary>
        /// An install with no window, for CI: every event to stdout, exit 0 only
        /// if it reached the end.
        ///
        /// Honoured only in a sandboxed run, for the reason the manifest
        /// override is: a shipped install has no path to anything a test needs.
        /// It stops before pairing, which needs a person, and the sandbox has
        /// already made the login item a no-op -- so what it proves is the rest:
        /// the embedded uv unpacks, the pinned helper installs on real Windows
        /// from PyPI, `pythonw.exe` is where the task will look for it, and the
        /// helper answers `status --porcelain` in the shape the window reads.
        /// </summary>
        private static int Headless()
        {
            InstallerEvent? last = null;
            Installer.Install(ev =>
            {
                last = ev;
                switch (ev)
                {
                    case InstallerEvent.Step s: Console.WriteLine("step: " + s.Title + " | " + s.Detail); break;
                    case InstallerEvent.Notice n: Console.WriteLine("notice: " + n.Text); break;
                    case InstallerEvent.Finished f: Console.WriteLine("finished: " + f.Message); break;
                    case InstallerEvent.Failed f: Console.WriteLine("failed: " + f.Message); break;
                    default: Console.WriteLine("event: " + ev.GetType().Name); break;
                }
            }, pair: false);
            Console.Out.Flush();
            return last is InstallerEvent.Finished ? 0 : 1;
        }

        /// <summary>Run a sequence on a background thread, remembering it for Try Again.</summary>
        private static void Run(Action<Action<InstallerEvent>>? next = null)
        {
            if (next != null) action = next;
            var sequence = action;
            var target = window!;
            // Background, so closing the window ends the program rather than
            // leaving an invisible installer running with nothing to report to.
            new Thread(() => sequence(target.Render)) { IsBackground = true }.Start();
        }
    }
}
