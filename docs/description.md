# AnkiTube

Earn media watch time by reviewing flashcards!

Accrued time appears as cubes that fall over the Anki window and pile above the review bottom bar. Each answered card adds one cube worth your configured seconds. As you spend watch time, cubes disappear (not necessarily oldest-first).

By default on macOS and Windows, **Anki Media Timer** pauses system media when your budget runs out and keeps re-pausing while you are out of time (optionally even after Anki quits). Play and Pause live under **Tools → AnkiTube** and the `P` hotkey. An embedded YouTube queue/player remains available as a legacy option in Settings.

## Requirements

- Anki 2.1.45 or newer
- macOS or Windows 10/11 for system media control (default)
- Internet access if using legacy YouTube mode

## Settings

Open **Tools → AnkiTube → Settings...**

- **Seconds per card / cube** — watch time and cube size per answered card
- **Maximum watch budget** — upper limit on banked seconds
- **Auto-resume media when budget is restored** — off by default
- **Show Anki Media Timer icon** — on by default (menu bar on macOS, system tray on Windows); turn off to hide the icon while keeping pause enforcement
- **Quit with Anki** — on by default; turn off to keep pause enforcement after Anki closes
- **Use embedded YouTube player (legacy)** — restore the in-dock queue/player
- **Show dock in review only** — for legacy player dock visibility
- **Dock side** — left or right (legacy player)
- **Debug logging** — write diagnostic events to `ankittube.log`

## Potential Annoyances

- System lockout only covers apps that publish to macOS Now Playing or Windows SMTC; other audio sources may still play.
- Enforcement is best-effort (poll and pause), not a hard OS-level block.
- After Anki quits, lockout continues only when **Quit with Anki** is turned off.
- Private Now Playing APIs can change with macOS updates; SMTC coverage varies by app on Windows.
- In legacy YouTube mode, YouTube’s terms of service apply to embedded playback.

## Support

Please report issues and request features on GitHub: https://github.com/bl-ake/AnkiTube/issues
