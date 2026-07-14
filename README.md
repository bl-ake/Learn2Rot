# AnkiTube

[![CI](https://github.com/bl-ake/AnkiTube/actions/workflows/ci.yml/badge.svg)](https://github.com/bl-ake/AnkiTube/actions/workflows/ci.yml)

An Anki add-on that meters watch time you've earned by reviewing cards. By default on macOS, it observes and pauses system Now Playing media (Spotify, Music, browser tabs that report Now Playing, and similar). An embedded YouTube queue/player remains available as a legacy mode.

## Requirements

- Anki **2.1.45+** (see `manifest.json`)
- **macOS** for system media control (default mode)
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

Open **Tools → AnkiTube → Show Player** (or **Toggle Player**).

1. Review cards to earn watch-time budget
2. Play media in another app (or press Play in the dock)
3. When your budget runs out, AnkiTube pauses Now Playing and keeps re-pausing while you are out of time

Legacy YouTube mode: enable **Use embedded YouTube player (legacy)** in Settings, then add videos via paste, drag-and-drop, or `+`.

Settings are under **Tools → AnkiTube → Settings...**

Useful options:

- **Seconds earned per reviewed card** — how much watch time each card is worth (default: 15s)
- **Maximum watch budget** — cap on banked time (default: 10 minutes)
- **Auto-resume media when budget is restored** — off by default; when on, resumes after earning time following exhaustion
- **Use embedded YouTube player (legacy)** — restore the in-dock queue/player
- **Show dock in review only** — hide the dock outside the review screen
- **Dock side** — dock on the left or right side of the main window

### Limits (system media mode)

Lockout only applies to apps that publish to macOS Now Playing. Sources that do not register can still make sound. Enforcement is best-effort (poll + pause), not a hard OS block.

## Keyboard shortcuts

When the dock is open and you're not typing in a text field:

| Key | Action |
|-----|--------|
| `P` | Play / pause |
| Hold `Option` (macOS) / `Alt` | Temporarily pause |

Legacy YouTube mode also supports:

| Key | Action |
|-----|--------|
| `G` | Toggle fullscreen |
| `C` | Toggle captions |
| `←` / `→` | Seek backward / forward |
| `↑` / `↓` | Volume up / down |

## Debug logging

Enable **Debug logging** in settings, then check **Tools → AnkiTube → View Debug Log**. The log file lives in your Anki profile folder as `ankittube.log`.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Bug reports should use the GitHub issue template when possible.

- **YouTube** (legacy mode) uses the [YouTube IFrame Player API](https://developers.google.com/youtube/iframe_api_reference); video titles may be fetched via [YouTube oEmbed](https://oembed.com/).
- System media control on macOS uses private Now Playing APIs via `osascript` (best-effort; may change with OS updates).
