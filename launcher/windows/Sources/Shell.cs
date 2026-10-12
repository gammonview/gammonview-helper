// SPDX-License-Identifier: MIT
// Copyright (C) 2026 Nicholas Vlamis

using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

namespace GammonView.Installer
{
    public sealed class ShellException : Exception
    {
        public ShellException(string message, Exception? inner = null) : base(message, inner) { }

        public static ShellException CouldNotStart(string what, Exception why) =>
            new ShellException("Could not start " + what + ": " + why.Message, why);

        public static ShellException Failed(string what, int code, string tail) =>
            new ShellException(tail.Length == 0
                ? what + " failed (exit " + code + ")."
                : what + " failed.\n\n" + tail);
    }

    /// <summary>
    /// Complete lines out of a pipe that delivers arbitrary chunks.
    ///
    /// `\r` ends a line as well as `\n` because `uv` redraws its progress bar
    /// by returning to the start of one -- which is also why this exists at
    /// all rather than `OutputDataReceived`, which splits on `\n` only and
    /// would hold every progress redraw back until the download finished.
    /// </summary>
    public sealed class LineBuffer
    {
        private readonly List<byte> pending = new List<byte>();
        private readonly Action<string> emit;

        public LineBuffer(Action<string> emit) { this.emit = emit; }

        public void Feed(byte[] data, int count)
        {
            for (var k = 0; k < count; k++)
            {
                var b = data[k];
                if (b == 0x0A || b == 0x0D) { Deliver(); continue; }
                pending.Add(b);
            }
        }

        /// <summary>Whatever arrived without a newline after it.</summary>
        public void Flush() => Deliver();

        private void Deliver()
        {
            if (pending.Count == 0) return;
            var text = Encoding.UTF8.GetString(pending.ToArray()).Trim(' ', '\t');
            pending.Clear();
            if (text.Length > 0) emit(text);
        }
    }

    public static class Shell
    {
        /// <summary>Strip the escape sequences `uv` colours its output with.</summary>
        public static string Plain(string line)
        {
            var sb = new StringBuilder();
            var inEscape = false;
            foreach (var c in line)
            {
                if (inEscape) { if (char.IsLetter(c)) inEscape = false; }
                else if (c == '\u001B') inEscape = true;
                else sb.Append(c);
            }
            return sb.ToString().Trim();
        }

        /// <summary>
        /// One argument, quoted the way `CommandLineToArgvW` and the MSVC runtime
        /// will split it back out.
        ///
        /// .NET Framework has no `ArgumentList`, only one command-line string,
        /// and the rules for building one are not the obvious ones: backslashes
        /// are literal *except* before a quote, so `C:\path with space\` naively
        /// quoted ends in `\"` and swallows the closing quote. Every path this
        /// program passes lives under the user's profile, and a profile folder
        /// is a person's name, so a space in one is ordinary.
        /// </summary>
        public static string Quote(string arg)
        {
            if (arg.Length > 0 && arg.IndexOfAny(new[] { ' ', '\t', '\n', '\v', '"' }) < 0)
                return arg;
            var sb = new StringBuilder("\"");
            var backslashes = 0;
            foreach (var c in arg)
            {
                if (c == '\\') { backslashes++; continue; }
                if (c == '"')
                {
                    sb.Append('\\', backslashes * 2 + 1);
                    sb.Append('"');
                }
                else
                {
                    sb.Append('\\', backslashes);
                    sb.Append(c);
                }
                backslashes = 0;
            }
            sb.Append('\\', backslashes * 2);
            sb.Append('"');
            return sb.ToString();
        }

        public static string CommandLine(IEnumerable<string> args) => string.Join(" ", args.Select(Quote));

        /// <summary>
        /// Run something to completion, streaming both its outputs. Returns the exit
        /// code, or throws if it was not zero and `check` is set.
        /// </summary>
        public static int Run(
            string executable,
            IEnumerable<string> arguments,
            string what,
            IDictionary<string, string>? environment = null,
            Action<string>? onOutput = null,
            Action<string>? onError = null,
            bool check = true)
        {
            var info = new ProcessStartInfo(executable, CommandLine(arguments))
            {
                UseShellExecute = false,
                // No console window for a console program run from a windowed one.
                // Without it every `uv` and every `status` flashes a black box.
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                RedirectStandardInput = true,
            };
            if (environment != null)
                foreach (var pair in environment) info.EnvironmentVariables[pair.Key] = pair.Value;

            var tail = new Queue<string>();
            var outBuffer = new LineBuffer(line => onOutput?.Invoke(line));
            var errBuffer = new LineBuffer(line =>
            {
                lock (tail)
                {
                    tail.Enqueue(line);
                    if (tail.Count > 6) tail.Dequeue();
                }
                onError?.Invoke(line);
            });

            Process process;
            try { process = Process.Start(info)!; }
            catch (Win32Exception e) { throw ShellException.CouldNotStart(what, e); }
            catch (InvalidOperationException e) { throw ShellException.CouldNotStart(what, e); }

            using (process)
            {
                process.StandardInput.Close();
                var outPump = Pump(process.StandardOutput.BaseStream, outBuffer);
                var errPump = Pump(process.StandardError.BaseStream, errBuffer);
                process.WaitForExit();
                // Drained after exit, not trusted to have finished: the last write
                // of a short-lived process is usually the error message.
                outPump.Join();
                errPump.Join();
                outBuffer.Flush();
                errBuffer.Flush();

                var code = process.ExitCode;
                if (code != 0 && check)
                {
                    string message;
                    lock (tail) message = string.Join("\n", tail.Select(Plain).Where(s => s.Length > 0));
                    throw ShellException.Failed(what, code, message);
                }
                return code;
            }
        }

        private static Thread Pump(Stream stream, LineBuffer buffer)
        {
            var thread = new Thread(() =>
            {
                var chunk = new byte[4096];
                int n;
                while ((n = stream.Read(chunk, 0, chunk.Length)) > 0) buffer.Feed(chunk, n);
            }) { IsBackground = true };
            thread.Start();
            return thread;
        }

        // MARK: - what Windows will not let us replace while it runs

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr OpenProcess(uint access, bool inherit, int pid);

        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern bool QueryFullProcessImageName(
            IntPtr process, int flags, StringBuilder name, ref int size);

        [DllImport("kernel32.dll")]
        private static extern bool CloseHandle(IntPtr handle);

        private const uint ProcessQueryLimitedInformation = 0x1000;

        private static string? ImagePath(Process p)
        {
            // Not `p.MainModule`: that reads the target's module list, which
            // fails across bitness -- and on an ARM64 machine this installer is
            // native while the Python it installed is x64 under emulation.
            var handle = OpenProcess(ProcessQueryLimitedInformation, false, p.Id);
            if (handle == IntPtr.Zero) return null;
            try
            {
                var size = 1024;
                var name = new StringBuilder(size);
                return QueryFullProcessImageName(handle, 0, name, ref size) ? name.ToString() : null;
            }
            finally { CloseHandle(handle); }
        }

        /// <summary>
        /// End every process whose executable lives under `dir`.
        ///
        /// **The one step macOS does not need.** There, `uv tool install --force`
        /// replaces files under a running helper and the old process carries on
        /// with the inodes it has open. Windows refuses: a loaded `.pyd` or
        /// `python313.dll` cannot be deleted, so updating a running helper fails
        /// halfway through with a venv half old and half new -- and an uninstall
        /// leaves exactly the files somebody asked to have removed. So both stop
        /// everything running out of the root first.
        ///
        /// By path, after the scheduled task has been ended, rather than trusting
        /// the task alone: a helper started by hand, or a `status` the installer
        /// itself ran and then lost, holds the same locks.
        /// </summary>
        public static void StopEverythingUnder(string dir)
        {
            var prefix = Path.GetFullPath(dir).TrimEnd('\\') + "\\";
            var self = Process.GetCurrentProcess().Id;
            foreach (var p in Process.GetProcesses())
            {
                using (p)
                {
                    if (p.Id == self) continue;
                    string? path;
                    try { path = ImagePath(p); } catch { continue; }
                    if (path == null || !path.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                        continue;
                    try
                    {
                        p.Kill();
                        p.WaitForExit(10000);
                    }
                    catch { /* already gone, or not ours to stop */ }
                }
            }
        }

        /// <summary>
        /// Delete a tree, harder than `Directory.Delete` does.
        ///
        /// Two Windows habits defeat the plain call: read-only attributes, which
        /// some packages ship files with, and an antivirus scanner holding a file
        /// it has just been told was written. One retry after a pause outlasts
        /// the second. Best-effort, as everything in an uninstall is.
        /// </summary>
        public static void DeleteTree(string dir)
        {
            for (var attempt = 0; attempt < 3; attempt++)
            {
                if (!Directory.Exists(dir)) return;
                try
                {
                    foreach (var file in Directory.EnumerateFiles(dir, "*", SearchOption.AllDirectories))
                    {
                        try { File.SetAttributes(file, FileAttributes.Normal); } catch { }
                    }
                    Directory.Delete(dir, true);
                    return;
                }
                catch (IOException) { Thread.Sleep(1500); }
                catch (UnauthorizedAccessException) { Thread.Sleep(1500); }
            }
        }
    }
}
