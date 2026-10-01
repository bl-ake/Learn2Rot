# Learn2Rot

[![CI](https://github.com/bl-ake/Learn2Rot/actions/workflows/ci.yml/badge.svg)](https://github.com/bl-ake/Learn2Rot/actions/workflows/ci.yml)

Make your brainrot work for YOU!

This is an Anki add-on that lets you earn screen time by doing your flashcards, every answer gives you a couple precious seconds. Playing media will use up your time, and when it runs out, it'll auto-pause until you do more flashcards. Works for Youtube, TikTok, Spotify, and anything else that you can play/pause with the button on your keyboard. There's also cute little squares that pile up to show you your accrued time but you can turn those off if you want, there's lots of options you can set!

The old version of this add-on embeds a YouTube player in an Anki panel, which is still available in the add-on's settings but I'm probably gonna remove it since Firefox already lets you have a floating player. 

## Requirements

- Anki **2.1.45+** (see `manifest.json`)
- **macOS** or **Windows 10/11** for system media control (default mode)
- Internet access if using legacy YouTube mode

## Install

### From GitHub Releases

1. Open the [Releases](https://github.com/bl-ake/Learn2Rot/releases) page
2. Download the latest `Learn2Rot.ankiaddon` asset
3. Double-click the file or use **Tools → Add-ons → Install from file…**
4. Restart Anki if prompted

### From AnkiWeb

Install through Anki’s add-on manager with the add-on code, or install a downloaded `.ankiaddon` file via **Tools → Add-ons → Install from file…**

### From source

1. Download or clone this repo
2. Put the folder in your Anki add-ons directory:
   - **macOS:** `~/Library/Application Support/Anki2/addons21/`
   - **Windows:** `%APPDATA%\Anki2\addons21\`
   - **Linux:** `~/.local/share/Anki2/addons21/`
3. Restart Anki

The folder name can be anything, but `Learn2Rot` keeps things simple.

For local packaging without bumping the AnkiWeb `mod` timestamp:

```bash
python package.py --no-update-mod
```

## Usage

1. Review cards — each answer drops a cube worth **seconds per card** of watch time (including cards reviewed on AnkiMobile / other devices after you sync)
2. Cubes fall and collect above Anki’s review bottom bar (drag them around if you like)
3. Play media (macOS Now Playing / Windows SMTC); budget drains while playing and cubes disappear
4. When time runs out, Learn2Rot pauses system media and keeps re-pausing while you’re out of time

A small **Watch:** timer in the review overlay (top-left) shows time remaining. On macOS and Windows, the same countdown appears in the menu bar (macOS) or system tray (Windows) by default via **Anki Media Timer**, a background helper that also owns budget drain and pause enforcement (toggle the icon in Settings). Optionally keep it running after Anki quits (**Quit with Anki** off) so media stays locked out until you earn more time.

### Only when cards are due (off by default)

Turn on **Only when cards are due** and it changes what happens once your banked time runs out — not before. Budget still counts down normally either way; but if it hits zero while you have nothing left to review, locking media would just strand you, so Anki Media Timer backs off instead — no pausing, icon hidden — until cards are due again. It checks again right after you answer a card, undo one, or sync, and otherwise about once a minute, so it comes back on its own once cards are due (the next day, or once a learning card's timer elapses) without needing to reopen Anki. Only Anki can query the collection, so while it's closed the helper instead relies on Anki's last prediction of when the next card would become due (day rollover or a learning card) recorded just before it closed — enforcement resumes automatically once that time passes, even without reopening Anki.

### Login settings (both off by default)

**Quit with Anki** only covers the stretch after you close Anki — reboot and nothing is watching until you open it again. Two settings close that gap, both registering a small per-user login item (a LaunchAgent on macOS, a `Run` entry on Windows):

- **Open at login** opens Anki Media Timer the moment you log in — countdown, budget drain and pause enforcement start right away, without waiting for anything to play. On its own, the login item runs once and exits, so a later quit (or running out of budget) stays quit until your next login — there's nothing left resident to revive it.
- **Persist in background** keeps that login item running afterward instead of exiting, so it revives Anki Media Timer whenever media starts — including after you quit it, or if you never open Anki at all. Turning this on also turns on **Open at login**, since a watcher that stays running but never shows itself right away isn't very useful.

Turning both off removes the login item entirely. While Anki is running, Anki owns the timer so you never get two running at once. What the watcher does is appended to `learn2rot_sentinel.log` in your Anki profile folder.

**Restrict to hours** sets a daily window (e.g. 09:00–21:00, or overnight ranges like 22:00–06:00), plus which days of the week it applies on — and works whether or not Persist in background is on. Outside the window (or on an unchecked day), Anki Media Timer backs off exactly like it does when no cards are due: no pausing, icon hidden, budget left untouched — even for a timer that's already running — and it picks back up on its own once the window reopens, no restart needed. With **Persist in background** also on, the watcher keeps running the whole time too, but won't revive the timer outside the window, so quitting it there stays quit, even once media starts playing again, until the window opens or you reopen Anki yourself. Day checks use the calendar day the moment falls on, so an overnight window can cut off at midnight if the next day isn't also checked. Unchecking every day backs off entirely while leaving the watcher itself running.

## Debug logging

Enable **Debug logging** on the **Advanced** tab of Settings, then use its **View Log** / **Clear Log** buttons. The log file lives in your Anki profile folder as `learn2rot.log`.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Bug reports should use the GitHub issue template when possible.

- **YouTube** (legacy mode) uses the [YouTube IFrame Player API](https://developers.youtube.com/youtube/iframe_api_reference); video titles may be fetched via [YouTube oEmbed](https://oembed.com/).
- System media control on macOS uses private Now Playing APIs via `osascript` (best-effort; may change with OS updates).
- System media control on Windows uses [System Media Transport Controls (SMTC)](https://learn.microsoft.com/en-us/uwp/api/windows.media.control.globalsystemmediatransportcontrolssessionmanager) via PyWinRT.
