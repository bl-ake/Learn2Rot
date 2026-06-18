# AnkiTube

[![CI](https://github.com/bl-ake/AnkiTube/actions/workflows/ci.yml/badge.svg)](https://github.com/bl-ake/AnkiTube/actions/workflows/ci.yml)

An Anki add-on that lets you watch YouTube in a dock panel, but only with time you've earned by reviewing cards.

This add-on embeds a YouTube player inside Anki that gives you a couple seconds of watch time per card answered. Videos are played from a queue you can build by pasting URLs, dragging-and-dropping, or using the `+` button.

## Requirements

- Anki **2.1.45+** (see `manifest.json`)
- Internet access for YouTube

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

1. Add videos to the queue
2. Review some cards to earn watch time
3. Hit play

Drag YouTube links onto the dock or queue to add them. Drop on the top half of the panel to insert at the front of the queue.

Settings are under **Tools → AnkiTube → Settings...**

Useful options:

- **Seconds earned per reviewed card** — how much watch time each card is worth (default: 15s)
- **Maximum watch budget** — cap on banked time (default: 10 minutes)
- **Show dock in review only** — hide the player outside the review screen
- **Dock side** — dock on the left or right side of the main window

## Keyboard shortcuts

When the player is open and you're not typing in a text field:

| Key | Action |
|-----|--------|
| `P` | Play / pause |
| `G` | Toggle fullscreen |
| `C` | Toggle captions |
| `←` / `→` | Seek backward / forward |
| `↑` / `↓` | Volume up / down |
| Hold `Option` (macOS) / `Alt` | Temporarily pause |

## Debug logging

Enable **Debug logging** in settings, then check **Tools → AnkiTube → View Debug Log**. The log file lives in your Anki profile folder as `ankittube.log`.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Bug reports should use the GitHub issue template when possible.

- **YouTube** playback uses the [YouTube IFrame Player API](https://developers.google.com/youtube/iframe_api_reference); video titles may be fetched via [YouTube oEmbed](https://oembed.com/).
