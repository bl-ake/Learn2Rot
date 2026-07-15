# AnkiTube

[![CI](https://github.com/bl-ake/AnkiTube/actions/workflows/ci.yml/badge.svg)](https://github.com/bl-ake/AnkiTube/actions/workflows/ci.yml)

An Anki add-on that meters watch time you've earned by reviewing cards. Accrued time appears as cubes that fall over the Anki window, bounce off the flashcard, and pile at the bottom. On macOS, AnkiTube can pause system Now Playing media when your budget runs out. An embedded YouTube queue/player remains available as a legacy mode.

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

1. Review cards — each answer drops a cube worth **seconds per card** of watch time
2. Cubes bounce off the card surface and collect above Anki’s review bottom bar
3. Play media (macOS Now Playing); budget drains while playing and cubes disappear
4. When time runs out, AnkiTube pauses Now Playing and keeps re-pausing while you’re out of time

A small **Watch:** timer in the review overlay (top-left) shows time remaining. On macOS, the same countdown appears in the menu bar by default via a small helper process (toggle in Settings).

Controls:

- **Tools → AnkiTube → Play / Pause / Play/Pause**
- Keyboard: `P` play/pause; hold `Option` (macOS) / `Alt` to temporarily pause

Legacy YouTube mode: enable **Use embedded YouTube player (legacy)** in Settings, then **Show Player** for the dock queue/player.

Settings are under **Tools → AnkiTube → Settings...**

Useful options:

- **Seconds per card / cube** — earn amount and cube size (default: 15s)
- **Maximum watch budget** — cap on banked time (default: 10 minutes)
- **Auto-resume media when budget is restored** — off by default
- **Show remaining watch time in the menu bar** — on by default (macOS)
- **Use embedded YouTube player (legacy)** — restore the in-dock queue/player

### Limits

- Cube lockout / pause only covers apps that publish to macOS Now Playing
- Card colliders use `#qa` / `.card` bounding boxes; unusual templates may not bounce perfectly
- Overlay is mouse-transparent so Anki stays fully clickable

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

- **YouTube** (legacy mode) uses the [YouTube IFrame Player API](https://developers.google.com/youtube/iframe_api_reference); video titles may be fetched via [YouTube oEmbed](https://oembed.com/).
- System media control on macOS uses private Now Playing APIs via `osascript` (best-effort; may change with OS updates).
