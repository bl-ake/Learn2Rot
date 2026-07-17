# AnkiTube

[![CI](https://github.com/bl-ake/AnkiTube/actions/workflows/ci.yml/badge.svg)](https://github.com/bl-ake/AnkiTube/actions/workflows/ci.yml)

Make your brainrot work for YOU!

An Anki add-on that meters watch time you've earned by reviewing cards. Playing media will use up your time, and when it runs out, it'll auto-pause until you do more flashcards. Works for Youtube, TikTok, Spotify, and anything else that you can play/pause with the button on your keyboard. There's also cute little squares that pile up to show you your accrued time but you can turn those off if you want.

The old version of this add-on embeds a YouTube player in an Anki panel, which is still available in the add-on's settings but I'm probably gonna remove it since Firefox already lets you have a floating player. 

## Requirements

- Anki **2.1.45+** (see `manifest.json`)
- **macOS** or **Windows 10/11** for system media control (default mode)
- Internet access if using legacy YouTube mode

## Install

### From GitHub Releases

1. Open the [Releases](https://github.com/bl-ake/AnkiTube/releases) page
2. Download the latest `AnkiTube.ankiaddon` asset
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

The folder name can be anything, but `AnkiTube` keeps things simple.

For local packaging without bumping the AnkiWeb `mod` timestamp:

```bash
python package.py --no-update-mod
```

## Usage

1. Review cards — each answer drops a cube worth **seconds per card** of watch time
2. Cubes fall and collect above Anki’s review bottom bar (drag them around if you like)
3. Play media (macOS Now Playing / Windows SMTC); budget drains while playing and cubes disappear
4. When time runs out, AnkiTube pauses system media and keeps re-pausing while you’re out of time

A small **Watch:** timer in the review overlay (top-left) shows time remaining. On macOS and Windows, the same countdown appears in the menu bar (macOS) or system tray (Windows) by default via **Anki Media Timer**, a background helper that also owns budget drain and pause enforcement (toggle the icon in Settings). Optionally keep it running after Anki quits (**Quit with Anki** off) so media stays locked out until you earn more time.

## Debug logging

Enable **Debug logging** in settings, then check **Tools → AnkiTube → View Debug Log**. The log file lives in your Anki profile folder as `ankittube.log`.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Bug reports should use the GitHub issue template when possible.

- **YouTube** (legacy mode) uses the [YouTube IFrame Player API](https://developers.youtube.com/youtube/iframe_api_reference); video titles may be fetched via [YouTube oEmbed](https://oembed.com/).
- System media control on macOS uses private Now Playing APIs via `osascript` (best-effort; may change with OS updates).
- System media control on Windows uses [System Media Transport Controls (SMTC)](https://learn.microsoft.com/en-us/uwp/api/windows.media.control.globalsystemmediatransportcontrolssessionmanager) via PyWinRT.
