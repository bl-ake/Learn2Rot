# AnkiTube

Earn media watch time by reviewing flashcards!

Accrued time appears as cubes that fall over the Anki window, bounce off the flashcard, and pile at the bottom. Each answered card adds one cube worth your configured seconds. As you spend watch time, cubes disappear (not necessarily oldest-first).

By default on macOS, **Anki Media Timer** pauses system Now Playing media when your budget runs out and keeps re-pausing while you are out of time (optionally even after Anki quits). Play and Pause live under **Tools → AnkiTube** and the `P` hotkey. An embedded YouTube queue/player remains available as a legacy option in Settings.

## Requirements

- Anki 2.1.45 or newer
- macOS for system media control (default)
- Internet access if using legacy YouTube mode

## Settings

Open **Tools → AnkiTube → Settings...**

- **Seconds per card / cube** — watch time and cube size per answered card
- **Maximum watch budget** — upper limit on banked seconds
- **Auto-resume media when budget is restored** — off by default
- **Show Anki Media Timer icon in the menu bar** — on by default (macOS); turn off to hide the icon while keeping pause enforcement
- **Quit with Anki** — on by default; turn off to keep pause enforcement after Anki closes (macOS)
- **Use embedded YouTube player (legacy)** — restore the in-dock queue/player
- **Show dock in review only** — for legacy player dock visibility
- **Dock side** — left or right (legacy player)
- **Debug logging** — write diagnostic events to `ankittube.log`

## Potential Annoyances

- System lockout only covers apps that publish to macOS Now Playing; other audio sources may still play.
- Enforcement is best-effort (poll and pause), not a hard OS-level block.
- After Anki quits, lockout continues only when **Quit with Anki** is turned off.
- Card bounce uses the main card element (`#qa` / `.card`); some templates may not provide a useful collider.
- Private Now Playing APIs can change with macOS updates.
- In legacy YouTube mode, YouTube’s terms of service apply to embedded playback.

## Support

Please report issues and request features on GitHub: https://github.com/bl-ake/AnkiTube/issues
