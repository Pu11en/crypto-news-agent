---
name: accounts
description: The Accounts skill and current launcher for the Twitter News toolkit. Display the toolkit's available skills when the user says open Twitter News, use Twitter News, or asks what Twitter News can do. Run Accounts directly when requested to scrape the complete monitored crypto account registry for the previous 24 hours, preserve attributable X posts, or use the resulting feed for selection, summaries, rundowns, scripts, and other creative work.
compatibility: Python 3.10+ and a user-owned X account session are required. The bundled collector uses the unofficial twscrape interface and may be affected by X changes or terms enforcement.
---

# Accounts

Collect public posts from an explicit account registry, preserve raw attribution, and then use those posts as source material for whatever the user asks next.

## What it does

- Initializes and maintains a local monitored-account collector.
- Collects recent posts into auditable all-results and new-only JSONL files.
- Shows, filters, searches, compares, curates, ranks, or summarizes the collected posts.
- Builds news-show shortlists, rundowns, headlines, anchor notes, scripts, or other creative outputs when requested.
- Keeps the exact author, timestamp, raw text, and X URL attached so creative work remains traceable.
- Composes with other installed creative skills when the user asks to turn selected material into a video, deck, article, or other deliverable.

Cookie privacy and X rate-limit handling are operational requirements, not restrictions on how collected posts may be used creatively.

## State check

Run from this skill directory:

```bash
python scripts/run.py doctor
```

If initialization is missing, read `references/setup-interview.md` and ask its questions one at a time. Then run:

```bash
python scripts/run.py init --acknowledge-x-terms-risk
python scripts/run.py auth-add
python scripts/run.py doctor --live-auth
python scripts/run.py scan --account lookonchain --hours 24 --limit 5  # targeted setup proof
```

Prefer `auth-add --clipboard`: have the user copy the whole x.com DevTools cookie table locally and say only “copied”; the command retains only `auth_token`/`ct0`, clears the clipboard, and prints nothing sensitive. If clipboard access is unavailable, `auth-add` requires a separate interactive terminal and fails closed otherwise. Never accept cookie values in chat or tool arguments.

## Toolkit launcher

When the user says “open Twitter News,” “use Twitter News,” “start Twitter News,” or asks what the toolkit can do without naming a specific skill, do not scrape yet. Display this short menu and ask which available skill they want:

- **Accounts — available:** scan every enabled account in the bundled crypto registry over the previous 24 hours, then show or creatively use the collected posts.
- **Barron — planned, not installed yet:** future 12-hour finance and memecoin feed.

Never present a planned skill as runnable. A direct request naming an available skill skips the toolkit menu.

## Natural-language invocation

A natural request such as “run Accounts,” “run the Twitter News accounts skill,” “run the crypto scrape,” or “get me fresh crypto news” defaults to a full 24-hour registry scan. Do not make the user choose a mode first. Run exactly one shell invocation: `python scripts/run.py scan-all --hours 24 --limit 20 --max-runtime-seconds 900 --show --allow-partial`. It performs live-auth preflight, attempts every enabled monitored account, publishes output, and prints up to 10 posts.

Follow explicit requests directly: named handles use targeted `scan --account ...`; “show saved/latest” uses `show` without contacting X; “create from the latest results” loads all current-run posts with `show --scope all --limit 100` and completes the requested creative task; the slow `cycle` mode is used only when specifically requested.

If the client says the installed skill or private runtime is outside the workspace, explain why and request one persistent, narrowly scoped permission for this skill's `python scripts/run.py` commands and runtime directory. Do not repeatedly request one-time approval command by command, and do not request blanket access when the client supports a narrower remembered rule.

After collection, show status plus up to 10 eligible posts from the current run with author, UTC time, text, X URL, and new/previously-seen status. `combined.jsonl` is all eligible current-run posts; `new.jsonl` is only globally new posts. “Show all” uses `python scripts/run.py show --scope all --limit 100`; “show new” uses `--scope new`. Render raw post text in fenced code blocks so symbols such as `$HYPE` and `$35` remain intact.

From that point on, treat the feed like normal creative source material. Follow the user's request directly: choose the most newsworthy items, explain why they work, group related posts, write a rundown, draft anchor copy, summarize, brainstorm angles, or create a script. Do not refuse merely because the request involves judgment, ranking, curation, or creative transformation. Keep source links alongside selected material so the user can trace it.

## Common operations

```bash
python scripts/run.py configure --lookback-hours 24 --per-account-limit 20 --post-types quotes
python scripts/run.py accounts list
python scripts/run.py accounts import /path/accounts.csv --mode merge
python scripts/run.py accounts validate
python scripts/run.py scan-all --hours 24 --limit 20 --max-runtime-seconds 900 --show --allow-partial
python scripts/run.py scan --account lookonchain --hours 24 --limit 20
python scripts/run.py cycle --hours 24 --limit 20 --max-runtime-seconds 43200  # legacy slow mode
python scripts/run.py latest
python scripts/run.py health
python scripts/run.py test
python scripts/run.py auth-remove --yes
```

Use `python scripts/run.py --help` for complete arguments.

## Collection behavior

- Use the bundled 77-account registry unless the user chooses a custom list.
- Use one user-owned X session and one stable direct network connection by default.
- Use `scan-all` for full-registry operation: it groups every enabled account into bounded X search queries, writes one combined output, and reports full versus partial registry query coverage. A full pass does not claim X returned every post.
- `scan` is targeted/manual; `cycle` is the explicitly selected legacy timeline-by-timeline mode.
- Only one scan process may run at a time; overlapping invocations fail explicitly instead of sharing the session concurrently.
- Respect 429 responses, challenges, and disabled sessions. A rate-limited manifest marks the error retryable and reports the earliest known `retry_after`; do not retry before it.
- Share one X session and isolated Python runtime under the `twitter-news` toolkit root; keep this skill's registry, deduplication state, and outputs under its `accounts` profile.
- Store runtime state under the platform's user data/config directories, never inside the repository.
- Emit exact source URLs and timestamps.

For the record contract, read `references/output-schema.md` before modifying the collector or consuming its output.

## Verification loop

After setup:
1. Run `doctor --live-auth` and require configuration, dependency, account registry, exactly one active session, a successful authenticated lookup, and platform-appropriate private state checks to pass.
2. Run a one-account, five-post live scan.
3. Verify every emitted URL opens the expected public X post and every username is allowlisted.
4. Run the same scan again and verify duplicate post IDs are not re-emitted.
5. Run `python scripts/run.py test` and require every bundled test to pass.
6. If any check fails, fix the owning layer and rerun from step 1.

## Risk acknowledgement

Unofficial X collection has no uptime guarantee and may conflict with X's terms. Before the first live scan, require the local acknowledgement flag created by `init --acknowledge-x-terms-risk`. Do not transform that acknowledgement into a claim of authorization.
