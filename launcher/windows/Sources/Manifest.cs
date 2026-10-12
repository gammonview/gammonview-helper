// SPDX-License-Identifier: MIT
// Copyright (C) 2026 Nicholas Vlamis

using System;
using System.IO;
using System.Net;

namespace GammonView.Installer
{
    public sealed class ManifestException : Exception
    {
        public enum Kind { Unreachable, Malformed, LauncherTooOld }

        public Kind Reason { get; }

        public ManifestException(Kind reason, Exception? inner = null)
            : base(Describe(reason), inner)
        {
            Reason = reason;
        }

        private static string Describe(Kind reason)
        {
            switch (reason)
            {
                case Kind.Unreachable:
                    return "Could not reach gammonview.com.\n\n"
                        + "Check that this computer is online, then try again.";
                case Kind.Malformed:
                    return "gammonview.com sent something this installer could not read.";
                default:
                    return "This installer is out of date and cannot install the current helper.\n\n"
                        + "Download the current one from gammonview.com.";
            }
        }
    }

    /// <summary>
    /// Which version of the helper to install, as the site currently answers it.
    ///
    /// The same file, the same fields and the same reasoning as the macOS
    /// launcher's `Manifest.swift`, which carries that reasoning in full: the
    /// version, the Python and the site are all policy, policy moves, and a
    /// frozen binary must therefore fetch them rather than know them.
    /// </summary>
    public sealed class Manifest
    {
        /// <summary>
        /// What generation of launcher this is, compared against `launcher_min`.
        ///
        /// The same integer line as the macOS launcher's, deliberately: the
        /// manifest has one `launcher_min`, and it describes the *protocol*
        /// between a launcher and the helper, which is the same on both
        /// platforms. This one starts at 1 because it speaks exactly the
        /// protocol macOS generation 1 speaks.
        /// </summary>
        public const int LauncherGeneration = 1;

        public string Version { get; }
        public string? Python { get; }
        public string? Site { get; }
        public long LauncherMin { get; }
        public string? Notice { get; }

        public Manifest(string version, string? python, string? site, long launcherMin, string? notice)
        {
            Version = version;
            Python = python;
            Site = site;
            LauncherMin = launcherMin;
            Notice = notice;
        }

        /// <summary>
        /// A constant in a real install, and deliberately not configurable there.
        ///
        /// The override is honoured only when `GAMMONVIEW_INSTALLER_ROOT` is also
        /// set -- a run already told to install somewhere disposable and leave
        /// the login item alone. See `Manifest.url` in the macOS launcher.
        /// </summary>
        public static Uri Url
        {
            get
            {
                var overrideUrl = Environment.GetEnvironmentVariable("GAMMONVIEW_INSTALLER_MANIFEST");
                if (Paths.Sandboxed != null && Uri.TryCreate(overrideUrl, UriKind.Absolute, out var parsed))
                    return parsed;
                return new Uri("https://gammonview.com/helper-release.json");
            }
        }

        public static Manifest Fetch()
        {
            string body;
            try
            {
                var request = (HttpWebRequest)WebRequest.Create(Url);
                // A rollback that a cache can defer is not a rollback.
                request.CachePolicy = new System.Net.Cache.RequestCachePolicy(
                    System.Net.Cache.RequestCacheLevel.NoCacheNoStore);
                request.Timeout = 20000;
                request.UserAgent = "GammonView-Installer-Windows/" + LauncherGeneration;
                using (var response = (HttpWebResponse)request.GetResponse())
                using (var reader = new StreamReader(response.GetResponseStream()!))
                {
                    if (response.StatusCode != HttpStatusCode.OK)
                        throw new WebException("HTTP " + (int)response.StatusCode);
                    body = reader.ReadToEnd();
                }
            }
            catch (WebException e)
            {
                throw new ManifestException(ManifestException.Kind.Unreachable, e);
            }
            return Parse(body);
        }

        /// <summary>
        /// The decision half of <see cref="Fetch"/>, apart so it can be checked
        /// without a network.
        /// </summary>
        public static Manifest Parse(string body)
        {
            var o = Json.Object(body);
            var version = o == null ? null : Json.String(o, "version");
            var launcherMin = o == null ? null : Json.Integer(o, "launcher_min");
            if (o == null || version == null || launcherMin == null)
                throw new ManifestException(ManifestException.Kind.Malformed);

            if (launcherMin.Value > LauncherGeneration)
                throw new ManifestException(ManifestException.Kind.LauncherTooOld);

            // Unknown fields are ignored rather than rejected: the manifest is
            // allowed to grow, and a launcher frozen today must keep working
            // against one that has learned to say more.
            return new Manifest(
                version,
                Json.String(o, "python"),
                Json.String(o, "site"),
                launcherMin.Value,
                Json.String(o, "notice"));
        }
    }
}
