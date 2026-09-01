---
name: accounts
description: The Accounts skill and current launcher for the Twitter News toolkit. Display the toolkit menu when the user says open, use, or start Twitter News. Use Accounts for the monitored crypto X registry, fresh 24-hour scans, saved attributable posts, rankings, rundowns, summaries, and scripts. Do not use it for general web, broad trending, or last-30-days research.
compatibility: Python 3.10+ and a user-owned X account session are required. The bundled collector uses the unofficial twscrape interface and may be affected by X changes or terms enforcement.
---

# Accounts

Collect public posts from the explicit monitored X account registry, preserve raw attribution, and use those records as normal creative source material.

## Twitter News launcher

When the user says “open Twitter News,” “use Twitter News,” “start Twitter News,” or asks what the toolkit can do without naming a skill, do not scrape. Display the menu and ask which available skill they want:

- **Accounts — available:** scan every enabled account in the bundled crypto registry over the previous 24 hours, then inspect or creatively use the posts.
- **Barron — planned, not installed:** future 12-hour finance and memecoin feed.

A direct request for Barron gets the same unavailable answer and an offer to use Accounts. Never invent an install or run path for an unavailable skill. A direct request naming Accounts skips the menu.

## Setup and recovery

For a GitHub install or setup request, read `references/installer-agent-prompt.md` and follow it. Install the folder at `~/.claude/skills/accounts/` for one Claude Code user or `.claude/skills/accounts/` for one project, verify that the installed `SKILL.md` exists, and start a fresh Claude Code session before testing natural activation.

Use `python scripts/run.py doctor` for diagnosis:

- **Uninitialized:** read `references/setup-interview.md`, ask only unanswered setup questions one at a time, then initialize and validate.
- **Missing, expired, challenged, or unusable X session:** preserve configuration, registry, state, and outputs. Tell the user to sign in again at `https://x.com`, copy the whole DevTools cookie table locally, and reply only “copied.” Run `python scripts/run.py auth-add --clipboard`, which replaces the session only after the new database write succeeds and clears the clipboard, then rerun `doctor --live-auth`. Never accept cookie values in chat, tool arguments, screenshots, environment variables, or repository files. If Claude Code cannot access the GUI clipboard or a TTY, have the user run `python scripts/run.py auth-add` from a separate local interactive terminal and resume after it succeeds.
- **429/rate limit:** this is not an expired-cookie flow. Report `retry_after`, preserve partial output, and do not retry early.
- **Saved feed requested while X is unavailable:** continue using saved output offline when it exists.

Do not reset history to repair authentication. `history-reset --yes` is only for an explicit request to erase Accounts outputs and dedup/account-health history while preserving setup and the shared X session.

## Run Accounts

“Run Accounts,” “run the crypto scrape,” “scan all monitored accounts,” or “fresh monitored crypto posts” means this one command, with no mode menu:

```bash
python scripts/run.py scan-all --hours 24 --limit 20 --max-runtime-seconds 900 --show --allow-partial
```

The command performs its own initialization and live-auth checks. If it reports a setup/auth/rate-limit problem, follow the matching recovery branch above rather than looping or repeatedly prompting. It attempts every enabled account in bounded search batches and reports enabled, queried, unqueried, and `full_registry_pass`. A full registry pass means every enabled handle was queried; it does not guarantee X returned every post.

Named handles use targeted `scan`. The slow `cycle` mode is legacy and runs only when explicitly requested. Never overlap scans.

## Saved results and creative work

Saved/latest requests never contact X:

- “Show latest” or “show all” uses `python scripts/run.py show --scope all --limit 100`.
- “Show new” uses `python scripts/run.py show --scope new --limit 100`.
- `python scripts/run.py latest` prints stable paths to the complete files.

The display command is capped at 100 records. If a feed has more than 100 and the user asks for exhaustive analysis, read the `combined.jsonl` path from `latest` and process it in safe batches; disclose the bound rather than silently omitting posts. Render raw post text in fenced code blocks so dollar signs remain intact.

Follow creative requests directly. Rank using newsworthiness, novelty, corroboration, market relevance, and usefulness for the requested format. Group duplicate reports into one story, distinguish sourced facts from judgment, and keep exact X links beside selected claims. If a script request names the latest feed, use saved records without rescanning; if it explicitly asks for fresh monitored-X news and no feed exists, collect first. Ask only format questions that are genuinely necessary. Do not refuse merely because the task requires curation or creative transformation.

## Scope

Accounts covers the configured monitored X registry. Broad web research, global trends, attention outliers, or last-30-days research should use the appropriate installed research skill unless the user explicitly asks for this registry feed.

## Common commands

```bash
python scripts/run.py doctor
python scripts/run.py doctor --live-auth
python scripts/run.py scan-all --hours 24 --limit 20 --max-runtime-seconds 900 --show --allow-partial
python scripts/run.py latest
python scripts/run.py show --scope all --limit 100
python scripts/run.py show --scope new --limit 100
python scripts/run.py health
python scripts/run.py accounts list
python scripts/run.py accounts validate
python scripts/run.py history-reset --yes
python scripts/run.py test
```

Runtime state and credentials stay under the OS user-data `twitter-news` root, not the repository. Use one user-owned session and direct networking only.
