# Portable installer-agent prompt

Give the prompt below to Claude Code, Codex, or another coding agent running locally on the recipient's computer.

---

Install and validate `crypto-signal-scan` from this repository. It collects raw public X posts from a bundled crypto account registry using exactly one user-owned X session and no paid API.

## Non-negotiable safety

- Read `skills/crypto-signal-scan/SKILL.md`, `references/setup-interview.md`, and `references/output-schema.md` before acting.
- Never ask for or accept cookies in chat, tool arguments, environment variables, screenshots, or repository files.
- Never receive cookie values in chat. The easiest safe flow is local clipboard import: the user copies the x.com DevTools cookie table and says only “copied”; run `python scripts/run.py auth-add --clipboard`. The script keeps only `auth_token`/`ct0`, clears the clipboard, and never prints them. If local clipboard access is unavailable, use the separate interactive-terminal fallback.
- Use one legitimately controlled X session, direct networking, and no proxy rotation, identity rotation, CAPTCHA bypass, purchased cookies, or account farming.
- Explain before initialization that twscrape uses X's unofficial interface, can break, and may be enforced against under X's terms. Continue only after explicit acceptance.

## Install

1. Verify Python 3.10+ and local terminal access. On Debian/Ubuntu, install the matching `python3-venv` package if venv creation is unavailable.
2. Copy `skills/crypto-signal-scan/` into the client's documented local skill directory, preserving all files. If the client snapshots skills at startup, tell the user a restart/new session may be required for natural-language skill activation. Direct script setup can continue now.
3. From the installed skill directory run:

   ```bash
   python scripts/run.py init --acknowledge-x-terms-risk
   python scripts/run.py doctor
   ```

   Plain `doctor` is pre-auth readiness and must return `ready: true`, `readiness_stage: pre-auth`. It is expected to report zero sessions at this point.

## Interview and configuration

Ask exactly one multiple-choice question at a time using `references/setup-interview.md`. Apply answers only through supported commands:

```bash
python scripts/run.py configure --lookback-hours 24 --per-account-limit 20 --post-types quotes
python scripts/run.py accounts validate
```

For additional accounts, create a CSV with headers `username,tier,tags,enabled`, validate it, then merge it:

```bash
python scripts/run.py accounts import /absolute/path/accounts.csv --mode merge
```

Use `--mode replace` only when the user explicitly chooses a custom-only registry; it must retain at least one enabled account. `accounts sync-bundled` updates from the bundled registry. Never hand-edit private runtime JSON or SQLite.

## Secure cookie handoff

Tell the user:

1. Sign in to `https://x.com` in Chrome/Chromium/Edge or Firefox.
2. Chrome-family: Developer Tools → Application → Cookies → `https://x.com`. Firefox: Developer Tools → Storage → Cookies.
3. Click inside the cookie table, select/copy the whole table to the local clipboard, and reply only **“copied”**. Never paste the table into chat.
4. Immediately run from the installed skill directory:

   ```bash
   python scripts/run.py auth-add --clipboard
   ```

   This locally extracts only `auth_token` and `ct0`, stores them privately, clears the clipboard, and prints no cookie values.
5. If clipboard access is unavailable, instruct the user to open a separate terminal and run `python scripts/run.py auth-add`, entering only the two values in hidden prompts.

Then run:

```bash
python scripts/run.py doctor --live-auth
```

Require `ready: true`, `readiness_stage: live-auth`, exactly one active session, and `live_auth_verified: true`.

## Live proof

Run a minimal enabled-account scan. Start with:

```bash
python scripts/run.py scan --account lookonchain --hours 24 --limit 20
```

If it reaches the account but emits zero eligible posts, select another enabled active account or widen the lookback/limit. Do not treat zero eligible posts as authentication failure. Once at least one record is emitted, repeat the exact scan after any reported `retry_after`; verify existing post IDs are counted as duplicates and not re-emitted.

Run all tests:

```bash
python scripts/run.py test
```

## Everyday operation and outputs

Use the bounded resumable cycle rather than one account per day:

```bash
python scripts/run.py cycle --hours 24 --limit 20 --max-runtime-seconds 43200
```

The cycle uses one account at a time, waits until known endpoint retry times when they fit inside the runtime budget, persists progress, and stops safely on session loss. It does not bypass limits and does not promise complete X coverage. If interrupted or deadline-limited, run it again later; it resumes from the persisted cursor.

Show the user:

```bash
python scripts/run.py latest
python scripts/run.py health
```

`latest` prints stable paths to `combined.jsonl` (new raw posts from the latest cycle) and `cycle-manifest.json` (coverage, rounds, failures, retry/deadline status). Open both for the user and explain that JSONL is one JSON object per post with exact X URL, author, text, and timestamps.

To delete the local X session physically:

```bash
python scripts/run.py auth-remove --yes
```

## New-session natural-language activation test

After setup and validation, tell the user to close this agent session, start a new Claude Code/Codex session, and send exactly:

> Scan my monitored crypto accounts now and show me the new raw posts with author, time, text, and X link.

The new agent should automatically load this skill, run live-auth doctor, execute the short interactive cycle defined in `SKILL.md`, and display up to 10 raw posts plus the manifest/output paths. If the client does not auto-load it, verify the client skill directory and restart once; direct script operation remains available.

## Completion report

Return:
- installed skill and runtime paths;
- pre-auth and live-auth doctor results without credential values;
- configured defaults and enabled-account count;
- custom account import result;
- live scan reached/new/error counts;
- duplicate-suppression result;
- test pass total;
- cycle/latest commands and output paths;
- any remaining retry time, unavailable handles, client-restart requirement, and unofficial-X warning.

Do not claim permanent reliability, complete post coverage, or X authorization.

---
