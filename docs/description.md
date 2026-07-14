# AnkiTube

Earn media watch time by reviewing flashcards!

By default on macOS, AnkiTube meters a review-earned time budget against system Now Playing media (Spotify, Music, browser tabs that report Now Playing, and similar). Playback is paused when your budget runs out, and AnkiTube keeps re-pausing while you are out of time. An embedded YouTube queue/player remains available as a legacy option in Settings.

## Requirements

- Anki 2.1.45 or newer
- macOS for system media control (default)
- Internet access if using legacy YouTube mode

## Settings

Open **Tools → AnkiTube → Settings...**

- **Seconds earned per reviewed card** — watch time granted per answered card
- **Maximum watch budget** — upper limit on banked seconds
- **Auto-resume media when budget is restored** — off by default
- **Use embedded YouTube player (legacy)** — restore the in-dock queue/player
- **Show dock in review only** — hide the dock outside review mode
- **Dock side** — left or right dock area
- **YouTube controls / fullscreen** — embedded player chrome (legacy mode)
- **Dock playback buttons** — Play / Pause on the dock
- **Debug logging** — write diagnostic events to `ankittube.log`

## Potential Annoyances

- System lockout only covers apps that publish to macOS Now Playing; other audio sources may still play.
- Enforcement is best-effort (poll and pause), not a hard OS-level block.
- Private Now Playing APIs can change with macOS updates.
- In legacy YouTube mode, video duration is estimated from page metadata when available; YouTube’s terms of service apply to embedded playback.

## Support

Please report issues and request features on GitHub: https://github.com/bl-ake/AnkiTube/issues
