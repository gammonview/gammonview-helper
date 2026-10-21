// SPDX-License-Identifier: MIT
// Copyright (C) 2026 Nicholas Vlamis

using System;
using System.Diagnostics;
using System.Drawing;
using System.Windows.Forms;

namespace GammonView.Installer
{
    /// <summary>
    /// The one window. `Window.swift` on macOS, in WinForms.
    ///
    /// Deliberately plain, for the same reason: the installer looks finished
    /// long before the ~80 MB has arrived, so there is always a sentence saying
    /// what is happening and a bar that moves until something has ended.
    ///
    /// Colours are `SystemColors` throughout, so a high-contrast theme -- which
    /// this audience is likelier than most to be using -- is honoured for free.
    /// </summary>
    public sealed class InstallerWindow : Form
    {
        private readonly Label headline = new Label();
        private readonly Label stepTitle = new Label();
        private readonly Label detail = new Label();
        private readonly Label notice = new Label();
        private readonly ProgressBar progress = new ProgressBar();
        private readonly Panel wordCard = new Panel();
        private readonly TextBox wordLabel = new TextBox();
        private readonly Label wordCaption = new Label();
        private readonly TextBox linkLabel = new TextBox();
        private readonly Button primary = new Button();
        private readonly Button alternate = new Button();
        private readonly Button secondary = new Button();

        private Action? primaryAction;
        private Action? alternateAction;

        public Action? OnRetry;
        public Action? OnUpdate;
        public Action? OnUninstall;

        public InstallerWindow()
        {
            // Laid out in 96-dpi pixels and scaled once, as the designer would.
            // `app.manifest` declares the process DPI-aware so Windows stops
            // bitmap-stretching it, which hands the scaling to us: without these
            // two lines the fonts would grow on a 150% display and the boxes
            // around them would not.
            SuspendLayout();
            AutoScaleDimensions = new SizeF(96f, 96f);
            AutoScaleMode = AutoScaleMode.Dpi;
            Text = "GammonView Helper";
            Font = BodyFont();
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            ClientSize = new Size(560, 430);
            BackColor = SystemColors.Window;
            ForeColor = SystemColors.WindowText;
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }
            Build();
            ResumeLayout(false);
        }

        /// <summary>
        /// The body font, handed to every fixed-size label rather than inherited.
        ///
        /// A label with `AutoSize = false` that took the form's font was scaled
        /// twice: at 200% the detail line came out 4x as tall as laid out, and,
        /// being above the word card, painted it over -- the confirmation word
        /// was simply not on screen. Labels with a font of their own, and every
        /// other control, scaled once. Seen on a real Windows 11 laptop; never on
        /// CI, whose runner has no display scaling to double.
        /// </summary>
        private static Font BodyFont() => new Font("Segoe UI", 9.75f);

        private void Build()
        {
            const int inset = 28;
            var width = ClientSize.Width - 2 * inset;

            headline.Text = "GammonView Helper";
            headline.Font = new Font("Segoe UI Semibold", 15f);
            headline.AutoSize = true;
            headline.Location = new Point(inset, 24);

            stepTitle.Font = new Font("Segoe UI Semibold", 11f);
            stepTitle.AutoSize = false;
            stepTitle.SetBounds(inset, 72, width, 26);

            // Fixed height: a detail line that grows from one line to three
            // would move the bar and the buttons under the pointer.
            detail.Font = BodyFont();
            detail.ForeColor = SystemColors.GrayText;
            detail.AutoSize = false;
            detail.SetBounds(inset, 100, width, 92);

            progress.Style = ProgressBarStyle.Marquee;
            progress.MarqueeAnimationSpeed = 30;
            progress.SetBounds(inset, 196, width, 14);

            notice.Font = BodyFont();
            notice.ForeColor = Color.DarkOrange;
            notice.AutoSize = false;
            notice.SetBounds(inset, 216, width, 36);
            notice.Visible = false;

            wordCard.BorderStyle = BorderStyle.FixedSingle;
            wordCard.BackColor = SystemColors.Control;
            wordCard.SetBounds(inset, 220, width, 70);
            wordCard.Visible = false;

            // A read-only text box rather than a label: selectable so it can be
            // copied and read aloud by a screen reader, never editable, since it
            // is a value to compare and not one to type.
            wordLabel.ReadOnly = true;
            wordLabel.BorderStyle = BorderStyle.None;
            wordLabel.BackColor = SystemColors.Control;
            wordLabel.ForeColor = SystemColors.ControlText;
            wordLabel.TextAlign = HorizontalAlignment.Center;
            wordLabel.Font = new Font("Consolas", 26f, FontStyle.Bold);
            wordLabel.TabStop = false;
            wordLabel.SetBounds(0, 12, width - 2, 44);
            wordCard.Controls.Add(wordLabel);

            wordCaption.Font = BodyFont();
            wordCaption.ForeColor = SystemColors.GrayText;
            wordCaption.TextAlign = ContentAlignment.MiddleCenter;
            wordCaption.AutoSize = false;
            wordCaption.SetBounds(inset, 296, width, 24);
            wordCaption.Visible = false;

            // The page, written out, for a browser other than the default one or
            // a device other than this one. A read-only text box for the same
            // reason as the word: it can be selected and copied by hand.
            linkLabel.ReadOnly = true;
            linkLabel.BorderStyle = BorderStyle.None;
            linkLabel.BackColor = SystemColors.Window;
            linkLabel.ForeColor = SystemColors.GrayText;
            linkLabel.TextAlign = HorizontalAlignment.Center;
            linkLabel.Font = BodyFont();
            linkLabel.TabStop = false;
            linkLabel.SetBounds(inset, 326, width, 22);
            linkLabel.Visible = false;

            // Windows order: the default action, then Close, at the right. The
            // destructive alternate sits alone at the left and is never the
            // AcceptButton -- Enter must not uninstall.
            var buttonY = ClientSize.Height - 24 - 32;
            secondary.Text = "Quit";
            secondary.SetBounds(ClientSize.Width - inset - 110, buttonY, 110, 32);
            secondary.Click += (s, e) => Close();

            primary.SetBounds(ClientSize.Width - inset - 110 - 10 - 180, buttonY, 180, 32);
            primary.Visible = false;
            primary.Click += (s, e) => primaryAction?.Invoke();

            alternate.SetBounds(inset, buttonY, 120, 32);
            alternate.Visible = false;
            alternate.Click += (s, e) => alternateAction?.Invoke();

            foreach (var b in new[] { primary, secondary, alternate })
            {
                b.UseVisualStyleBackColor = true;
                b.BackColor = SystemColors.Control;
            }

            Controls.AddRange(new Control[]
            {
                headline, stepTitle, detail, progress, notice, wordCard, wordCaption,
                linkLabel, primary, secondary, alternate,
            });
            CancelButton = secondary;
        }

        /// <summary>Show an event. Safe to call from any thread.</summary>
        public void Render(InstallerEvent ev)
        {
            if (IsDisposed) return;
            if (InvokeRequired) { BeginInvoke(new Action(() => Render(ev))); return; }

            switch (ev)
            {
                case InstallerEvent.Step step:
                    stepTitle.Text = step.Title;
                    detail.Text = step.Detail;
                    break;

                case InstallerEvent.Notice n:
                    notice.Text = n.Text;
                    notice.Visible = true;
                    break;

                case InstallerEvent.Word word:
                    // Offered, not opened: the default browser is not always the
                    // one somebody signs in with, and the page works from any device.
                    stepTitle.Text = "Link this computer to your account";
                    detail.Text = "Open the page below in a browser, on this computer or any "
                        + "other device. Sign in if it asks, then choose this word:";
                    notice.Visible = false;
                    wordLabel.Text = word.Text;
                    wordCard.Visible = true;
                    wordCaption.Text = "Three words will be offered. Only this one is right.";
                    wordCaption.Visible = true;
                    linkLabel.Text = word.Url;
                    linkLabel.Visible = true;
                    SetPrimary("Open in browser", () => Open(word.Url));
                    SetAlternate("Copy link", () => CopyLink(word.Url));
                    break;

                case InstallerEvent.Choice choice:
                    StopProgress();
                    stepTitle.Text = "Already installed";
                    detail.Text = choice.Message;
                    // Update is the default: the common reason to run this twice,
                    // and the one that undoes nothing.
                    SetPrimary("Update", () => { BeginWork("Getting ready"); OnUpdate?.Invoke(); });
                    SetAlternate("Uninstall", () => { BeginWork("Removing"); OnUninstall?.Invoke(); });
                    break;

                case InstallerEvent.Finished finished:
                    StopProgress();
                    stepTitle.Text = finished.Title;
                    detail.Text = finished.Message;
                    HideLink();
                    secondary.Text = "Close";
                    SetPrimary("Done", Close);
                    break;

                case InstallerEvent.Failed failed:
                    StopProgress();
                    stepTitle.Text = "Something went wrong";
                    detail.Text = failed.Message;
                    HideLink();
                    if (failed.DownloadPage)
                        SetPrimary("Open gammonview.com", () => Open("https://gammonview.com"));
                    else if (failed.Retry)
                        SetPrimary("Try again", () => { BeginWork("Getting ready"); OnRetry?.Invoke(); });
                    else
                        primary.Visible = false;
                    break;
            }
        }

        /// <summary>Off the screen once linking has ended, Copy link with it.</summary>
        private void HideLink()
        {
            wordCard.Visible = false;
            wordCaption.Visible = false;
            linkLabel.Visible = false;
            alternate.Visible = false;
        }

        private void CopyLink(string url)
        {
            try { Clipboard.SetText(url); alternate.Text = "Copied"; }
            catch { /* the clipboard is busy; the link is on screen to copy by hand */ }
        }

        private static void Open(string url)
        {
            try { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true })?.Dispose(); }
            catch { /* no default browser; the word and the button are still on screen */ }
        }

        /// <summary>The detail label's working height, once the window has been scaled.</summary>
        private int? detailHeight;

        /// <summary>
        /// The bar goes, and the sentence takes its room: the screens without a
        /// bar are the ones with the most to say -- an uninstall's outcome, or a
        /// failure with the last lines `uv` printed.
        /// </summary>
        private void StopProgress()
        {
            progress.Visible = false;
            progress.Style = ProgressBarStyle.Blocks;
            detailHeight ??= detail.Height;
            detail.Height = Math.Max(detailHeight.Value, primary.Top - detail.Top - 12);
        }

        private void SetPrimary(string title, Action action)
        {
            primary.Text = title;
            primary.Visible = true;
            primaryAction = action;
            AcceptButton = primary;
            primary.Focus();
        }

        private void SetAlternate(string title, Action action)
        {
            alternate.Text = title;
            alternate.Visible = true;
            alternateAction = action;
        }

        /// <summary>
        /// Back to the working look. Hiding the buttons is the part that matters:
        /// a second press would run minutes of work concurrently with itself.
        /// </summary>
        private void BeginWork(string title)
        {
            primary.Visible = false;
            alternate.Visible = false;
            notice.Visible = false;
            AcceptButton = null;
            secondary.Text = "Quit";
            if (detailHeight.HasValue) detail.Height = detailHeight.Value;
            progress.Style = ProgressBarStyle.Marquee;
            progress.Visible = true;
            stepTitle.Text = title;
            detail.Text = "";
        }
    }
}
