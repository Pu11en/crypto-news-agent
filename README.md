# crypto-news-agent

## Standalone zero-cost X collector: Crypto Signal Scan

This repository includes [`crypto-signal-scan`](skills/crypto-signal-scan/), a portable Agent Skill that uses one user-owned X browser session to collect raw public posts from a bundled 77-account crypto registry without a paid API.

**Give this repository to Claude Code, Codex, or another local coding agent and send it:**

```text
Install and validate the crypto-signal-scan Agent Skill from this repository.
First read skills/crypto-signal-scan/references/installer-agent-prompt.md and follow it exactly.
Guide me one question at a time, keep cookie entry in the hidden local auth-add prompt, prove a live scan and duplicate suppression, run the tests, then show me the everyday scan command.
```

Start here:

- **Complete setup and everyday-use guide:** [`docs/CRYPTO_SIGNAL_SCAN.md`](docs/CRYPTO_SIGNAL_SCAN.md)
- **Full coding-agent installation prompt:** [`skills/crypto-signal-scan/references/installer-agent-prompt.md`](skills/crypto-signal-scan/references/installer-agent-prompt.md)
- **Packaged Agent Skill:** [`dist/crypto-signal-scan.skill`](dist/crypto-signal-scan.skill)
- **Bundled monitored accounts:** [`skills/crypto-signal-scan/assets/accounts.csv`](skills/crypto-signal-scan/assets/accounts.csv)

Daily command after installation:

From the installed `crypto-signal-scan` skill directory:

```bash
python scripts/run.py cycle --hours 24 --limit 20 --max-runtime-seconds 43200
```

Normal scans persistently rotate through one account at a time, suppress duplicate post IDs, and print a `retry_after` time when X rate-limits the session. Cookie values must never be pasted into agent chat. The interface is unofficial and cannot be guaranteed permanently stable or authorized by X.

---

The repository also contains the older Telegram crypto-news scraper and research agent, with an optional local script/video experimentation mode.

## Product modes

Set `BOT_MODE=scraper` for production. In this mode the bot:

- Uses natural-language requests only; production registers no slash-command
  handlers and clears Telegram's command menu at startup.
- Ask for fresh crypto news to request a new scrape. The bot asks for a natural
  language yes/no confirmation before spending Xquik credits.
- Scrapes only the accounts in `accounts.txt`, always through Xquik and never
  through direct X/Twitter access, broad X search, or alternate account lists.
- Ask to show saved scrapes, open the latest research, inspect raw posts, or check
  the Xquik credit balance.
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
              ├── llm.py            — DeepSeek V4 Flash with z.ai GLM fallback
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
export DEEPSEEK_API_KEY=...
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
| `DEEPSEEK_API_KEY` | yes | — | DeepSeek API key; primary LLM provider |
| `DEEPSEEK_BASE_URL` | no | `https://api.deepseek.com/v1` | OpenAI-compatible DeepSeek endpoint |
| `DEEPSEEK_MODEL` | no | `deepseek-v4-flash` | primary model name |
| `ZAI_API_KEY` | yes | — | z.ai GLM fallback key |
| `ZAI_BASE_URL` | no | `https://api.z.ai/api/coding/paas/v4` | fallback endpoint |
| `ZAI_MODEL` | no | `glm-5-turbo` | fallback model name |
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
