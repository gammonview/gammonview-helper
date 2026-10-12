// SPDX-License-Identifier: MIT
// Copyright (C) 2026 Nicholas Vlamis

//
// The Windows launcher's decisions, checked without installing anything.
//
// The macOS checks (`launcher/macos/Checks/main.swift`) ported case for case,
// since the two launchers read the same JSON from the same helper and must
// reach the same verdicts -- plus the two decisions only Windows has: how an
// argument is quoted onto a command line, and the scheduled task's definition.
//
// Run: launcher/windows/checks.sh
//

using System;
using System.Collections.Generic;
using System.Linq;
using System.Xml;
using GammonView.Installer;

var failures = 0;
void Check(string what, bool ok)
{
    Console.WriteLine(ok ? "  ok   " + what : "  FAIL " + what);
    if (!ok) failures++;
}
bool Same(IEnumerable<string> a, params string[] b) => a.SequenceEqual(b);

// --- LinkedVerdict: may the finished screen claim this machine is set up? ---

Console.WriteLine("LinkedVerdict -- is this machine already linked?");
Check("a working link is a link",
    Installer.LinkedVerdict(@"{""event"":""status"",""linked"":true,""link"":""working""}"));
Check("a revoked session is not", !Installer.LinkedVerdict(@"{""linked"":true,""link"":""revoked""}"));
Check("an unreachable relay is not",
    !Installer.LinkedVerdict(@"{""linked"":true,""link"":""unreachable""}"));
Check("a credential with no relay answer is not",
    !Installer.LinkedVerdict(@"{""linked"":true,""link"":null}"));
Check("no credential is not", !Installer.LinkedVerdict(@"{""linked"":false,""link"":null}"));
Check("a helper too old to report `link` falls back to `linked`",
    Installer.LinkedVerdict(@"{""event"":""status"",""linked"":true}"));
Check("...and that fallback still respects a false",
    !Installer.LinkedVerdict(@"{""event"":""status"",""linked"":false}"));
Check("an unknown link value is not a link",
    !Installer.LinkedVerdict(@"{""linked"":true,""link"":""something-new""}"));
Check("garbage is not a link", !Installer.LinkedVerdict("not json"));
Check("silence is not a link", !Installer.LinkedVerdict(null));
Check("an empty line is not a link", !Installer.LinkedVerdict(""));
Check("a JSON array neither crashes nor links", !Installer.LinkedVerdict("[1,2,3]"));
Check("a truncated object neither crashes nor links", !Installer.LinkedVerdict(@"{""link"":""work"));

// --- GlobalArguments / InstallArguments ---

Console.WriteLine("GlobalArguments -- the site comes from the manifest, never a literal");
Manifest M(string? site, string? python = "3.13") => new Manifest("0.8.0", python, site, 1, null);
Check("a named site is passed through",
    Same(Installer.GlobalArguments(M("https://gammonview.com")), "--site", "https://gammonview.com"));
Check("no site means no flag", Installer.GlobalArguments(M(null)).Count == 0);
Check("an empty site is not a site", Installer.GlobalArguments(M("")).Count == 0);

Console.WriteLine("InstallArguments -- the exact pinned version, on the pinned Python");
Check("pinned",
    Same(Installer.InstallArguments(M(null)),
        "tool", "install", "--force", "--python", "3.13", "gammonview-helper==0.8.0"));
Check("no python pin means uv chooses",
    Same(Installer.InstallArguments(M(null, null)),
        "tool", "install", "--force", "gammonview-helper==0.8.0"));

// --- UnlinkArguments ---

Console.WriteLine("UnlinkArguments -- and the older dialect the fallback speaks");
Check("porcelain, with a site",
    Same(Installer.UnlinkArguments(true, "https://gammonview.com"),
        "--site", "https://gammonview.com", "unlink", "--porcelain"));
Check("the fallback drops only the flag, never the site",
    Same(Installer.UnlinkArguments(false, "https://gammonview.com"),
        "--site", "https://gammonview.com", "unlink"));
Check("no site, porcelain", Same(Installer.UnlinkArguments(true, null), "unlink", "--porcelain"));
Check("an empty site is not a site here either", Same(Installer.UnlinkArguments(false, ""), "unlink"));

// --- AccountVerdict ---

Console.WriteLine("AccountVerdict -- what an uninstall is allowed to claim");
string Verdict(string? s)
{
    var v = Installer.AccountVerdict(s);
    return v.Kind == AccountOutcomeKind.Left ? "left:" + v.Reason : v.Kind.ToString().ToLowerInvariant();
}
Check("removed is reported",
    Verdict(@"{""event"":""unlinked"",""account"":""removed"",""reason"":null}") == "removed");
Check("left carries the reason",
    Verdict(@"{""event"":""unlinked"",""account"":""left"",""reason"":""no network""}") == "left:no network");
Check("left with no reason still has a sentence",
    Verdict(@"{""event"":""unlinked"",""account"":""left""}") == "left:could not reach gammonview.com");
Check("skipped is skipped", Verdict(@"{""account"":""skipped""}") == "skipped");
Check("a helper too old to report the field does not claim removal",
    Verdict(@"{""event"":""unlinked""}") == "skipped");
Check("a value we do not know does not claim removal", Verdict(@"{""account"":""new""}") == "skipped");
Check("garbage does not claim removal", Verdict("not json at all") == "skipped");
Check("silence does not claim removal", Verdict(null) == "skipped");
Check("a JSON array neither crashes nor claims removal", Verdict("[1,2,3]") == "skipped");

// --- PairingEvent: what `link --porcelain` lines become ---

Console.WriteLine("PairingEvent -- the device flow, line by line");
var offer = Installer.PairingEvent(
    @"{""event"":""offer"",""word"":""harbor"",""url"":""https://gammonview.com/link?c=x""}");
Check("an offer shows the word",
    offer is InstallerEvent.Word w && w.Text == "harbor" && w.Url.StartsWith("https://"));
Check("an offer whose url is a path is ignored -- the shell would run it",
    Installer.PairingEvent(@"{""event"":""offer"",""word"":""x"",""url"":""C:\\Windows\\notepad.exe""}") == null);
Check("an offer with no word is ignored",
    Installer.PairingEvent(@"{""event"":""offer"",""url"":""https://gammonview.com""}") == null);
Check("linked finishes", Installer.PairingEvent(@"{""event"":""linked""}") is InstallerEvent.Finished);
var failed = Installer.PairingEvent(@"{""event"":""failed"",""reason"":""expired"",""retry"":false}");
Check("failed carries its reason and its retry",
    failed is InstallerEvent.Failed f && f.Message == "expired" && !f.Retry);
Check("an unknown event is not an error", Installer.PairingEvent(@"{""event"":""progress""}") == null);
Check("uv's chatter is not an event", Installer.PairingEvent("Resolved 12 packages") == null);

// --- Manifest.Parse ---

Console.WriteLine("Manifest.Parse -- the file on gammonview.com");
var live = Manifest.Parse(@"{""version"":""0.8.0"",""python"":""3.13"",""site"":""https://gammonview.com"",
  ""launcher_min"":1,""installer"":""https://example.test/x.zip"",""notice"":null}");
Check("the live shape reads", live.Version == "0.8.0" && live.Python == "3.13"
    && live.Site == "https://gammonview.com" && live.LauncherMin == 1 && live.Notice == null);
Check("unknown fields are ignored",
    Manifest.Parse(@"{""version"":""1"",""launcher_min"":1,""future"":{""a"":[1,2]}}").Version == "1");
Check("1.0 is an integer, as Swift's `as? Int` reads it",
    Manifest.Parse(@"{""version"":""1"",""launcher_min"":1.0}").LauncherMin == 1);
ManifestException.Kind? Refusal(string body)
{
    try { Manifest.Parse(body); return null; }
    catch (ManifestException e) { return e.Reason; }
}
Check("a newer protocol refuses this launcher",
    Refusal(@"{""version"":""9"",""launcher_min"":2}") == ManifestException.Kind.LauncherTooOld);
Check("no version is malformed", Refusal(@"{""launcher_min"":1}") == ManifestException.Kind.Malformed);
Check("no launcher_min is malformed", Refusal(@"{""version"":""1""}") == ManifestException.Kind.Malformed);
Check("an HTML error page is malformed", Refusal("<html>502</html>") == ManifestException.Kind.Malformed);

// --- Shell.Quote: argv survives the trip through one command-line string ---

Console.WriteLine("Shell.Quote -- round-trips through the MSVC splitting rules");
foreach (var arg in new[]
{
    "plain", "", "with space", @"C:\Users\Ann Lee\AppData\Local\GammonView",
    @"C:\trailing slash\", "quote\"inside", @"back\\""slash", "https://gammonview.com",
    "gammonview-helper==0.8.0", "\ttab",
})
{
    var back = LoginItem.SplitCommandLine("first " + Shell.Quote(arg) + " last");
    Check("round-trips: " + (arg.Length == 0 ? "(empty)" : arg),
        back.Count == 3 && back[0] == "first" && back[1] == arg && back[2] == "last");
}
Check("a plain word is left alone", Shell.Quote("run") == "run");

// --- the scheduled task ---
//
// Round-tripped through the document `Install` actually writes, for the reason
// the macOS check round-trips its plist: the writer and the reader must agree.

Console.WriteLine("LoginItem -- the task Windows will run at every sign-in");
const string pythonw = @"C:\Users\Ann Lee\AppData\Local\GammonView\tools\gammonview-helper\Scripts\pythonw.exe";
const string root = @"C:\Users\Ann Lee\AppData\Local\GammonView";
const string sid = "S-1-5-21-1111111111-2222222222-3333333333-1001";
string Doc(string? site) => LoginItem.Document(pythonw, root, site, @"ANNS-PC\Ann Lee", sid);

Check("reads back exactly what install wrote",
    LoginItem.SiteFromDocument(Doc("https://beta.gammonview.com")) == "https://beta.gammonview.com");
Check("no site written means no site read", LoginItem.SiteFromDocument(Doc(null)) == null);
Check("an empty site is not a site", LoginItem.SiteFromDocument(Doc("")) == null);
Check("no document is not an error", LoginItem.SiteFromDocument(null) == null);
Check("an unparseable document is not an error", LoginItem.SiteFromDocument("ERROR: no such task") == null);

var xml = new XmlDocument();
var parsed = true;
try { xml.LoadXml(Doc("https://gammonview.com")); } catch (XmlException) { parsed = false; }
Check("the document is well-formed XML, despite the space and the backslashes", parsed);
if (parsed)
{
    var ns = new XmlNamespaceManager(xml.NameTable);
    ns.AddNamespace("t", "http://schemas.microsoft.com/windows/2004/02/mit/task");
    string? At(string path) => xml.SelectSingleNode("/t:Task/" + path, ns)?.InnerText;

    Check("it runs pythonw, never the console entry point", At("t:Actions/t:Exec/t:Command") == pythonw);
    Check("-m gvhelper, the site, then run",
        Same(LoginItem.SplitCommandLine(At("t:Actions/t:Exec/t:Arguments")!),
            "-m", "gvhelper", "--site", "https://gammonview.com", "run"));
    Check("as the user, without elevation",
        At("t:Principals/t:Principal/t:UserId") == sid
        && At("t:Principals/t:Principal/t:RunLevel") == "LeastPrivilege"
        && At("t:Principals/t:Principal/t:LogonType") == "InteractiveToken");
    Check("at this user's sign-in", At("t:Triggers/t:LogonTrigger/t:UserId") == @"ANNS-PC\Ann Lee");
    Check("unplugging the laptop does not stop it",
        At("t:Settings/t:DisallowStartIfOnBatteries") == "false"
        && At("t:Settings/t:StopIfGoingOnBatteries") == "false");
    Check("no three-day limit", At("t:Settings/t:ExecutionTimeLimit") == "PT0S");
    Check("restarted after a crash", At("t:Settings/t:RestartOnFailure/t:Count") == "999");
    Check("normal priority -- the helper lowers its own", At("t:Settings/t:Priority") == "6");
    Check("idle does not stop it", At("t:Settings/t:IdleSettings/t:StopOnIdleEnd") == "false");
}
Check("a name in XML's own syntax is escaped, not injected",
    LoginItem.SiteFromDocument(LoginItem.Document(pythonw, root, "https://x.test/?a=1&b=<2>",
        @"PC\Tom & Jerry", sid)) == "https://x.test/?a=1&b=<2>");
Check("the task name carries the SID, so two people on one PC do not collide",
    LoginItem.TaskName(sid) == "GammonView Helper " + sid);

// --- LineBuffer ---

Console.WriteLine("LineBuffer -- uv's progress redraws are lines too");
var lines = new List<string>();
var buffer = new LineBuffer(lines.Add);
var bytes = System.Text.Encoding.UTF8.GetBytes("Downloading 10%\rDownloading 20%\r\nDone\n  \npartial");
buffer.Feed(bytes, 7);
buffer.Feed(bytes.Skip(7).ToArray(), bytes.Length - 7);
Check("split on \\r and \\n, across chunks, blanks dropped",
    Same(lines, "Downloading 10%", "Downloading 20%", "Done"));
buffer.Flush();
Check("the unterminated tail is delivered at the end", lines.Last() == "partial");
Check("escape sequences are stripped", Shell.Plain("\u001B[2K\u001B[1mResolved\u001B[0m 3 packages") == "Resolved 3 packages");

// --- on Windows: does Task Scheduler itself accept the document? ---
//
// Everything above checks that the document says what we mean. Only Windows can
// say whether it is a task -- the schema is Task Scheduler's, and a document it
// refuses is an install that fails at its last step on every machine. So on a
// Windows runner the real thing is registered under a throwaway name, read back
// through `schtasks /Query /XML` (which is how `InstalledSite` reads it, so the
// re-serialised form is what gets parsed), and deleted. It is never run.

if (OperatingSystem.IsWindows())
{
    Console.WriteLine("Task Scheduler -- registering the real document, never running it");
    var me = System.Security.Principal.WindowsIdentity.GetCurrent();
    var name = "GammonView Checks " + Guid.NewGuid().ToString("N");
    var file = System.IO.Path.Combine(System.IO.Path.GetTempPath(), name + ".xml");
    var cmd = Environment.ExpandEnvironmentVariables(@"%SystemRoot%\System32\cmd.exe");
    System.IO.File.WriteAllText(file,
        LoginItem.Document(cmd, System.IO.Path.GetTempPath(), "https://beta.gammonview.com",
            me.Name, me.User!.Value),
        System.Text.Encoding.Unicode);
    try
    {
        var created = Shell.Run("schtasks.exe", new[] { "/Create", "/TN", name, "/XML", file, "/F" },
            "schtasks /Create", onError: Console.WriteLine, check: false);
        Check("schtasks /Create accepts it, without elevation", created == 0);

        var back = new System.Text.StringBuilder();
        Shell.Run("schtasks.exe", new[] { "/Query", "/TN", name, "/XML" }, "schtasks /Query",
            onOutput: line => back.AppendLine(line), check: false);
        Check("the site reads back out of what Windows stored",
            LoginItem.SiteFromDocument(back.ToString()) == "https://beta.gammonview.com");
        var stored = back.ToString();
        Check("Windows kept the battery settings",
            stored.Contains("<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>"));
        Check("Windows kept the restart policy", stored.Contains("<Count>999</Count>"));
    }
    finally
    {
        Shell.Run("schtasks.exe", new[] { "/Delete", "/TN", name, "/F" }, "schtasks /Delete", check: false);
        System.IO.File.Delete(file);
    }
}

Console.WriteLine(failures == 0 ? "\nall checks passed" : "\n" + failures + " FAILED");
return failures == 0 ? 0 : 1;
