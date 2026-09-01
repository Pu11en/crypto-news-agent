# Portable installer-agent prompt

Give the prompt below to Claude Code, Codex, or another coding agent running locally on the recipient's computer.

---

Install and validate `accounts` from this repository. It collects raw public X posts from a bundled crypto account registry using exactly one user-owned X session and no paid API.

## Non-negotiable safety

- Read `skills/accounts/SKILL.md`, `references/setup-interview.md`, and `references/output-schema.md` before acting.
- Never ask for or accept cookies in chat, tool arguments, environment variables, screenshots, or repository files.
- Never receive cookie values in chat. The easiest safe flow is local clipboard import: the user copies the x.com DevTools cookie table and says only “copied”; run `python scripts/run.py auth-add --clipboard`. The script keeps only `auth_token`/`ct0`, clears the clipboard, and never prints them. If local clipboard access is unavailable, use the separate interactive-terminal fallback.
- Use one legitimately controlled X session, direct networking, and no proxy rotation, identity rotation, CAPTCHA bypass, purchased cookies, or account farming.
- Accounts covers the configured monitored X registry, not broad web/trending research. Barron is planned but unavailable; never simulate it.
- Explain before initialization that twscrape uses X's unofficial interface, can break, and may be enforced against under X's terms. Continue only after explicit acceptance.

## Client permissions

Before setup, explain that the installed skill and its private runtime live outside the project workspace, so the coding client may request permission. In Claude Code, use `/permissions` for a narrow remembered rule such as `Bash(python scripts/run.py *)` only while the working directory is the installed Accounts folder; never recommend bypass-permissions mode. If the sandbox cannot reach the GUI clipboard or X network, use the separate local-terminal fallback rather than widening unrelated permissions. Other clients should use their persistent command/path allow mechanism. Do not trigger a chain of unexplained one-time prompts or request unrelated blanket shell access. Official references: [Claude Code skills](https://code.claude.com/docs/en/skills) and [permissions](https://code.claude.com/docs/en/permissions).

## Install

1. Verify Python 3.10+ and local terminal access. On Debian/Ubuntu, install the matching `python3-venv` package if venv creation is unavailable. If `python3` is the command providing Python 3.10+, use it consistently instead of `python`.
2. For Claude Code global installation, run `mkdir -p ~/.claude/skills && python -m zipfile -e dist/accounts.skill ~/.claude/skills` from the cloned repository. This must create `~/.claude/skills/accounts/SKILL.md`. The project-local alternative extracts into `<project-root>/.claude/skills/`. A source install may copy the complete `skills/accounts/` directory. Other clients use their documented skill directory.
3. Verify the installed `SKILL.md` exists and is the intended Accounts file; report its exact path. If the client snapshots skills at startup, tell the user a restart/new session is required for natural-language activation. Direct script setup can continue now.
4. From the installed skill directory run:

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

## Secure cookie handoff and recovery

Use the same flow for first authentication and an expired, challenged, or unusable session. During recovery preserve outputs, registry, configuration, and deduplication state; never run `init` or `history-reset` as an auth repair. A 429 is a separate retryable condition: retain partial output and wait until its exact `retry_after`.

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

Use the full-registry command for normal operation:

```bash
python scripts/run.py scan-all --hours 24 --limit 20 --max-runtime-seconds 900 --show --allow-partial
```

It includes every enabled account in safe batched X search queries and writes `combined.jsonl` with all eligible current-run posts plus `new.jsonl` with only globally unseen posts. The manifest must report eligible/new/previously-seen totals, enabled/queried/unqueried accounts, and `full_registry_pass`. Search is bounded/best-effort and does not promise X returned every post. Keep `cycle` only as an explicitly selected slow legacy timeline mode.

Show the user:

```bash
python scripts/run.py latest
python scripts/run.py health
```

`latest` prints stable paths to the latest combined, new-only, and registry-manifest files. Explain that combined contains all eligible posts from that scrape with `is_new`, while new-only contains only unseen IDs. If asked to show all, use `show --scope all --limit 100` and render post text in fenced code blocks so `$` symbols are not interpreted as math. This display is capped at 100; for an exhaustive larger-feed request, read the `combined.jsonl` path from `latest` and process it in batches while disclosing the bound.

To delete the local X session physically:

```bash
python scripts/run.py auth-remove --yes
```

## Everyday default

A natural request such as “run Accounts,” “run the Twitter News accounts skill,” or “run the crypto scrape” immediately runs the full 24-hour registry scan. Do not add a scrape-mode menu. Explicit account handles, saved-result requests, creative requests using the latest feed, and the slow legacy cycle override that default directly.

## Toolkit menu test

In a fresh session, “Open Twitter News” must display the available skill list without contacting X. It must show Accounts as available and Barron as planned/not installed. “Run Accounts” must skip that menu and immediately use the 24-hour full-registry default.

## New-session natural-language activation test

After setup and validation, tell the user to close this agent session, start a new Claude Code/Codex session, and send exactly:

> Scan my monitored crypto accounts now and show me the new raw posts with author, time, text, and X link.

The new agent should automatically load this skill, recognize that “monitored accounts” means the full-registry mode, execute `scan-all`, report registry coverage, and display up to 10 posts plus the output paths. Then test the creative handoff by saying:

> Look through all of those posts and pick the strongest ones for a crypto news show.

The agent should load all saved results and produce the requested shortlist instead of refusing because selection, ranking, summarization, or creative use is involved. If the client does not auto-load the skill, verify the client skill directory and restart once; direct script operation remains available.

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
