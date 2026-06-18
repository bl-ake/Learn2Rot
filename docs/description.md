# AnkiTube

Earn YouTube watch time by reviewing flashcards!

AnkiTube embeds a YouTube player in an Anki dock panel. You build a video queue, review cards to earn seconds of watch time, and playback pauses automatically when your budget runs out.

## Requirements

- Anki 2.1.45 or newer
- Internet access for YouTube

## Settings

Open **Tools → AnkiTube → Settings...**

- **Seconds earned per reviewed card** — watch time granted per answered card
- **Maximum watch budget** — upper limit on banked seconds
- **Show dock in review only** — hide the player outside review mode
- **Dock side** — left or right dock area
- **YouTube controls / fullscreen** — embedded player chrome
- **Dock playback buttons** — Play, Pause, Next, Fullscreen below the player
- **Debug logging** — write diagnostic events to `ankittube.log`

## Potential Annoyances

- Video duration is estimated from YouTube page metadata when available; if that fails, duration may be unknown until playback starts.
- YouTube’s terms of service and availability apply to all embedded playback.

## Support

Please report issues and request features on GitHub: https://github.com/bl-ake/AnkiTube/issues
