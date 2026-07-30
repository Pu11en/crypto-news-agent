# crypto-news-agent

A Telegram crypto-news scraper and research agent, with an optional local
script/video experimentation mode.

## Product modes

Set `BOT_MODE=scraper` for production. In this mode the bot:

- Scrapes curated crypto X accounts only after explicit confirmation.
- Saves every run and shows curated stories with exact source links and UTC times.
- Generates a validated, cached PDF with curated evidence and every raw post.
- Lets users browse all raw posts and discuss the latest scrape with a grounded
  research assistant.
- Does not generate scripts automatically after a scrape. A user can explicitly
  ask the grounded agent to draft and revise a script conversationally.
- Does not register video uploads, captions, storyboards, rendering, `/done`, or
  video callbacks.

Set `BOT_MODE=full` only for local script/video experiments. It keeps the
talking-notes and video pipeline available without changing production behavior.

## How scripts are written

Grounded in short-form video research:
- ≤150 words for a ~60-second read (shorter is better)
- Hook in the first 3–5 seconds — bold statement, question, or surprising stat
- Structure: Hook → Setup → 3 core beats → Payoff/CTA
- Written to be spoken aloud; cut filler ruthlessly

Sources: [Copy Posse](https://copyposse.com/blog/this-simple-short-form-video-script-formula-has-generated-millions-of-views/),
[Search Engine Journal](https://www.searchenginejournal.com/from-article-to-short-form-video-that-holds-attention/565238/),
[Leadde](https://leadde.ai/blog/how-to-write-video-script-templates-examples).

## Architecture

```
Telegram ⇆ Bot (async, single process)
              ├── handlers/
              │     ├── news.py     — /news flow (scrape → curate → script)
              │     ├── chat.py     — general chat + script refinement
              │     ├── commands.py — /done, /cancel
              │     └── session.py  — per-user state machine
              ├── xquik.py          — Xquik scraper (twitter data)
              ├── llm.py            — z.ai GLM client (OpenAI SDK)
              ├── prompts.py        — curation + script prompts
              ├── db.py             — SQLite (tweets, stories, scripts, sessions)
              ├── accounts.py       — account list loader
              └── config.py         — env-based settings
```

The exact production capability boundary and user flows are documented in [docs/PRODUCTION_AGENT.md](docs/PRODUCTION_AGENT.md).

## Local dev

```bash
# 1. Install deps
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Set env vars (see .env.example)
export ZAI_API_KEY=...
export XQUIK_API_KEY=...
export TELEGRAM_BOT_TOKEN=...
export ALLOWED_USER_IDS=<your-telegram-id>
export BOT_MODE=scraper         # production behavior; use full for local video tests
export DB_PATH=./data/agent.db   # local; /data/agent.db in prod

# 3. Run
python src/bot.py
```

Find your Telegram user ID by messaging [@userinfobot](https://t.me/userinfobot).

## Deploy to Railway

1. Push this repo to GitHub.
2. Railway → New Project → Deploy from GitHub → select the repo.
3. Add a **volume** mounted at `/data` (this is where `agent.db` lives —
   it survives redeploys).
4. Set the env vars from `.env.example` in the Railway dashboard.
5. Deploy. Watch logs for `Bot ready. Allowlist: (...)`.

## Env vars

| Var | Required | Default | Notes |
|---|---|---|---|
| `ZAI_API_KEY` | yes | — | z.ai GLM Coding Plan key |
| `ZAI_BASE_URL` | no | `https://api.z.ai/api/coding/paas/v4` | Coding Plan endpoint |
| `ZAI_MODEL` | no | `glm-4.6` | model name |
| `XQUIK_API_KEY` | yes | — | Xquik scraper key |
| `SCRAPE_HOURS` | no | `24` | lookback window |
| `MAX_TWEETS_PER_ACCOUNT` | no | `20` | per-account tweet cap |
| `TELEGRAM_BOT_TOKEN` | yes | — | from [@BotFather](https://t.me/BotFather) |
| `ALLOWED_USER_IDS` | yes | — | comma-separated Telegram IDs |
| `BOT_MODE` | no | `full` | `scraper` for production; `full` for local video experiments |
| `DB_PATH` | no | `/data/agent.db` | SQLite path |
| `REPORT_DIR` | no | `/data/reports` | persistent generated PDF directory |
| `REPORT_MAX_MB` | no | `45` | maximum PDF upload size |

## Files

```
accounts.txt          — curated crypto accounts (username,tier,tags)
requirements.txt
.env.example          — env var template
Dockerfile            — Railway deploy image
railway.toml          — Railway service config
src/                  — application code
```
