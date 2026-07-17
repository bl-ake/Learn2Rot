# AnkiTube

[![CI](https://github.com/bl-ake/AnkiTube/actions/workflows/ci.yml/badge.svg)](https://github.com/bl-ake/AnkiTube/actions/workflows/ci.yml)

An Anki add-on that meters watch time you've earned by reviewing cards. Accrued time appears as cubes that fall over the Anki window, bounce off the flashcard, and pile at the bottom. On macOS and Windows, AnkiTube can pause system media when your budget runs out. An embedded YouTube queue/player remains available as a legacy mode.

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
2. Cubes bounce off the card surface and collect above Anki’s review bottom bar (drag them around if you like)
3. Play media (macOS Now Playing / Windows SMTC); budget drains while playing and cubes disappear
4. When time runs out, AnkiTube pauses system media and keeps re-pausing while you’re out of time

A small **Watch:** timer in the review overlay (top-left) shows time remaining. On macOS and Windows, the same countdown appears in the menu bar (macOS) or system tray (Windows) by default via **Anki Media Timer**, a background helper that also owns budget drain and pause enforcement (toggle the icon in Settings). Optionally keep it running after Anki quits (**Quit with Anki** off) so media stays locked out until you earn more time.

Controls:

- **Tools → AnkiTube → Play / Pause / Play/Pause**
- Keyboard: `P` play/pause; hold `Option` (macOS) / `Alt` to temporarily pause

Legacy YouTube mode: enable **Use embedded YouTube player (legacy)** in Settings, then **Show Player** for the dock queue/player.

Settings are under **Tools → AnkiTube → Settings...**

Useful options:

- **Seconds per card / cube** — earn amount and cube size (default: 15s)
- **Maximum watch budget** — cap on banked time (default: 10 minutes)
- **Current watch budget** — shows remaining time; **Clear** resets it to zero
- **Auto-resume media when budget is restored** — off by default
- **Budget cubes** — show falling cubes over the Anki window (default on)
- **Cube drop bounds** — left/right % of the window width where cubes spawn (default: 0–100%)
- **Overlay timer** — show the **Watch:** countdown in the top-left (default on)
- **Show Anki Media Timer icon** — on by default (menu bar on macOS, system tray on Windows); turn off to hide the icon while keeping pause enforcement
- **Quit with Anki** — on by default; turn off to keep Anki Media Timer pausing media after Anki closes
- **Use embedded YouTube player (legacy)** — restore the in-dock queue/player

### Limits

- Cube lockout / pause only covers apps that publish to macOS Now Playing or Windows SMTC
- After Anki quits, lockout continues only when **Quit with Anki** is off (system mode)
- Card colliders use `#qa` / `.card` bounding boxes; unusual templates may not bounce perfectly
- Overlay is mouse-transparent except on cubes (so you can drag them); Anki stays clickable elsewhere

## Keyboard shortcuts

| Key | Action |
|-----|--------|
| `P` | Play / pause |
| Hold `Option` (macOS) / `Alt` | Temporarily pause |

Legacy YouTube mode also supports `G` fullscreen, `C` captions, arrows seek/volume.

## Debug logging

Enable **Debug logging** in settings, then check **Tools → AnkiTube → View Debug Log**. The log file lives in your Anki profile folder as `ankittube.log`.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Bug reports should use the GitHub issue template when possible.

- **YouTube** (legacy mode) uses the [YouTube IFrame Player API](https://developers.youtube.com/youtube/iframe_api_reference); video titles may be fetched via [YouTube oEmbed](https://oembed.com/).
- System media control on macOS uses private Now Playing APIs via `osascript` (best-effort; may change with OS updates).
- System media control on Windows uses [System Media Transport Controls (SMTC)](https://learn.microsoft.com/en-us/uwp/api/windows.media.control.globalsystemmediatransportcontrolssessionmanager) via PyWinRT.
