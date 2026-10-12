// SPDX-License-Identifier: MIT
// Copyright (C) 2026 Nicholas Vlamis

using System;
using System.IO;

namespace GammonView.Installer
{
    /// <summary>
    /// Everything the installer creates, and nothing outside it.
    ///
    /// One directory, plus a scheduled task, a Start menu shortcut and a
    /// Settings entry (`AppEntry`). As on macOS, deliberately not here: Program
    /// Files, the registry's PATH, the user's `uv`, their Python.
    /// Nothing needs administrator rights, and nothing asks for them.
    ///
    /// The root is `%LOCALAPPDATA%\GammonView`, which is also what
    /// `config_dir()` in `gvhelper/config.py` answers on Windows -- the same
    /// arrangement as macOS, where the install root and the helper's config
    /// directory are one directory. Logs live inside it too (`gvhelper` writes
    /// `logs\helper.log` there when it has no console), so deleting the root
    /// really is the whole uninstall.
    /// </summary>
    public static class Paths
    {
        /// <summary>
        /// Set `GAMMONVIEW_INSTALLER_ROOT` to install somewhere disposable.
        ///
        /// Moves the root *and* makes the login-item step a no-op, as on macOS:
        /// one variable because they are one decision. Not a supported
        /// interface and not mentioned to users.
        /// </summary>
        public static readonly string? Sandboxed = NonEmpty(
            Environment.GetEnvironmentVariable("GAMMONVIEW_INSTALLER_ROOT"));

        public static readonly string Root = Sandboxed != null
            ? Path.GetFullPath(Environment.ExpandEnvironmentVariables(Sandboxed))
            : Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "GammonView");

        /// <summary>`uv.exe`, and the `gammonview-helper.exe` entry point it writes.</summary>
        public static readonly string Bin = Path.Combine(Root, "bin");
        public static readonly string Uv = Path.Combine(Bin, "uv.exe");
        public static readonly string Helper = Path.Combine(Bin, "gammonview-helper.exe");
        /// <summary>This program's own copy, for Settings > Apps and the helper's Update. See `AppEntry`.</summary>
        public static readonly string Installer = Path.Combine(Bin, "GammonView-Installer.exe");

        /// <summary>`UV_TOOL_DIR`: the venv holding the helper and the engine.</summary>
        public static readonly string Tools = Path.Combine(Root, "tools");
        /// <summary>`UV_PYTHON_INSTALL_DIR`: the interpreter uv downloads for us.</summary>
        public static readonly string Python = Path.Combine(Root, "python");
        /// <summary>`UV_CACHE_DIR`, inside the root so deleting it reclaims the ~80 MB.</summary>
        public static readonly string Cache = Path.Combine(Root, "cache");
        public static readonly string Logs = Path.Combine(Root, "logs");

        /// <summary>
        /// The windowless interpreter in the helper's venv, which the login item runs.
        ///
        /// Not `gammonview-helper.exe`: that is a console-subsystem launcher, so
        /// Windows gives it a console window, and a login item would put a black
        /// window on the desktop at every sign-in that closing kills the helper.
        /// `pythonw.exe -m gvhelper` is the same program with no console at all.
        /// </summary>
        public static readonly string Pythonw =
            Path.Combine(Tools, "gammonview-helper", "Scripts", "pythonw.exe");

        public static void Create()
        {
            foreach (var dir in new[] { Root, Bin, Tools, Python, Cache, Logs })
                Directory.CreateDirectory(dir);
        }

        private static string? NonEmpty(string? s) => string.IsNullOrEmpty(s) ? null : s;
    }
}
