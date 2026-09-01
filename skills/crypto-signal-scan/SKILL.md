---
name: crypto-signal-scan
description: Collect fresh public X posts from a curated crypto account list into local JSONL and SQLite using one user-owned X browser session, without a paid API. Use whenever someone asks to scan crypto Twitter, scrape monitored crypto accounts, refresh a crypto feed, manage or validate a monitored-account registry, or export raw attributable posts. This skill only collects source posts; it does not rank stories, summarize claims, write scripts, or make videos.
compatibility: Python 3.10+ and a user-owned X account session are required. The bundled collector uses the unofficial twscrape interface and may be affected by X changes or terms enforcement.
---

# Crypto Signal Scan

Collect public posts from an explicit account registry. Preserve raw attribution and stop at collection.

## Boundary

This skill may:
- initialize a local collector;
- securely register one user-owned X session from cookies;
- list, add, disable, remove, and validate the syntax and uniqueness of monitored-account records;
- collect recent public posts;
- write normalized JSONL, a scan manifest, and local deduplication state;
- report authentication, account, and collection health.

This skill must not:
- score, rank, curate, summarize, or interpret posts;
- generate stories, scripts, videos, or trading advice;
- collect private posts, DMs, or unnecessary profile data;
- bypass CAPTCHA, challenges, rate limits, or account enforcement;
- acquire accounts, rotate identities, or download public proxy lists;
- print, echo, commit, or place cookie values in project files.

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
python scripts/run.py scan --hours 24  # scans the next account in the persistent round-robin
```

Prefer `auth-add --clipboard`: have the user copy the whole x.com DevTools cookie table locally and say only “copied”; the command retains only `auth_token`/`ct0`, clears the clipboard, and prints nothing sensitive. If clipboard access is unavailable, `auth-add` requires a separate interactive terminal and fails closed otherwise. Never accept cookie values in chat or tool arguments.

## Natural-language invocation

When the user says “scan my crypto accounts,” “refresh my crypto feed,” “show me the latest crypto posts,” or similar:

1. Run `python scripts/run.py doctor --live-auth`; if it is not ready, repair setup without requesting cookies in chat.
2. For an interactive result, run `python scripts/run.py cycle --hours 24 --limit 20 --max-runtime-seconds 120 --max-accounts 2`.
3. Run `python scripts/run.py latest`, read its `combined.jsonl` and manifest, and show the user the status plus up to 10 newly emitted posts verbatim as author, UTC time, text, and exact X URL. Do not summarize or rank them.
4. If no new posts were emitted, clearly distinguish duplicates, no eligible posts, rate limiting, and authentication failure. Give the exact `retry_after` when present.
5. Only launch the long 12-hour cycle when the user explicitly asks for the full daily registry run and understands the process must stay running.

## Common operations

```bash
python scripts/run.py configure --lookback-hours 24 --per-account-limit 20 --post-types quotes
python scripts/run.py accounts list
python scripts/run.py accounts import /path/accounts.csv --mode merge
python scripts/run.py accounts validate
python scripts/run.py scan --account lookonchain --hours 24 --limit 20
python scripts/run.py cycle --hours 24 --limit 20 --max-runtime-seconds 43200
python scripts/run.py latest
python scripts/run.py health
python scripts/run.py test
python scripts/run.py auth-remove --yes
```

Use `python scripts/run.py --help` for complete arguments.

## Collection behavior

- Use the bundled 77-account registry unless the user chooses a custom list.
- Use one user-owned X session and one stable direct network connection by default.
- Use `cycle` for everyday operation: it sequentially advances the persistent round-robin, honors known retry times inside a bounded runtime, publishes combined output, and resumes later. A single `scan` remains a targeted/manual operation.
- Only one scan process may run at a time; overlapping invocations fail explicitly instead of racing the cursor or sharing the session concurrently.
- Fetch individual user timelines with low concurrency and incremental local deduplication.
- Respect 429 responses, challenges, and disabled sessions. A rate-limited manifest marks the error retryable and reports the earliest known `retry_after`; do not retry before it.
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
