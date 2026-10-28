# medcomix-media

Files and small automations for the comic-style Reels channels **med.comix**, **agar.comix** and **tech.comix**.

The repository is **public on purpose**: Buffer takes each video from its `raw.githubusercontent.com` link when it
publishes a scheduled Reel. Do not make it private, or scheduled posts will fail. Do not put secrets here.

## Branches

| Branch | What is in it |
|---|---|
| `main` | the comment bot, Instagram stats, this README |
| `mc-media` | med.comix videos (`.mp4`) used by Buffer |
| `media` | agar.comix videos (`agarNN_*.mp4`) used by Buffer |
| `tech-media` | tech.comix videos used by Buffer |

A video link looks like `https://raw.githubusercontent.com/ai-analitik/medcomix-media/<branch>/<file>.mp4`.
Never rename or delete a video that is still scheduled in Buffer.

## Automations (`main`)

- **comment-bot** (`bot/comment_bot.py`, `.github/workflows/comment-bot.yml`) — every 10 minutes reads new comments
  under recent Reels and answers the viewer with a short reply written by Claude. Runs on schedule only when the
  repo variable `BOT_ENABLED` is `1`; a manual run is a dry run by default (shows replies, posts nothing).
  `bot/videos.json` holds what each video is about, so replies stay on topic.
- **insights** (`stats/insights.py`, `.github/workflows/insights.yml`) — every 6 hours saves Instagram numbers
  (views, reach, saves, shares, watch time, skip rate, audience) to `stats/data/`.

Secrets (Settings → Secrets and variables → Actions): `IG_TOKEN`, `IG_TOKEN_TECHCOMIX`, `IG_TOKEN_360COMIX`,
`ANTHROPIC_API_KEY`. Variables: `BOT_ENABLED`, `MODEL`, `WINDOW_HOURS`, `MAX_REPLIES`.
Instagram tokens last about 60 days.

`PRIVACY.md` — the privacy policy page required by the Instagram app.
