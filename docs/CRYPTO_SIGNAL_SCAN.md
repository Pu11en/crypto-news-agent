# Crypto Signal Scan

`crypto-signal-scan` is the standalone, zero-paid-API Agent Skill in this repository. It collects raw public X posts from a bundled 77-account crypto registry using one X session owned by the user.

It does not rank stories, summarize posts, write scripts, provide trading advice, or make videos.

## Fastest installation: give the repository to a coding agent

Clone or open this repository in Claude Code, Codex, or another local coding agent, then send the agent this prompt:

```text
Install and validate the crypto-signal-scan Agent Skill from this repository.
First read skills/crypto-signal-scan/references/installer-agent-prompt.md and follow it exactly.
Guide me through setup with one multiple-choice question at a time.
Never ask me to paste X cookies into chat. Have me copy the x.com DevTools cookie table locally and reply only “copied,” then use auth-add --clipboard; use the hidden terminal prompt only as fallback.
Do not finish until doctor --live-auth passes, a one-account live scan succeeds, the duplicate-suppression check is complete, and the bundled tests pass.
After setup, show me the single command I should run each day and explain retry_after without claiming X scraping is permanently reliable.
```

The complete portable agent instructions are in [`skills/crypto-signal-scan/references/installer-agent-prompt.md`](../skills/crypto-signal-scan/references/installer-agent-prompt.md).

## Requirements

- Python 3.10 or newer.
- Git, or a downloaded copy of this repository.
- One X account that the user legitimately controls.
- The user must already be able to sign in to `https://x.com` in their normal browser.
- A local terminal. Cookie values must not be pasted into an agent chat.

No paid X API, proxy list, account rotation, CAPTCHA bypass, or purchased session is required or supported.

## Manual installation

From the repository root:

```bash
cd skills/crypto-signal-scan
python scripts/run.py init --acknowledge-x-terms-risk
python scripts/run.py accounts validate
python scripts/run.py doctor
```

The first `run.py` invocation creates an isolated Python environment and installs the pinned `twscrape==0.20.1` dependency. Runtime state and credentials are stored outside the repository in the operating system's user-data directory.

## Get the two X cookies safely

1. Sign in to `https://x.com` in the browser normally.
2. Open browser Developer Tools.
3. In Chrome, Chromium, or Edge, open **Application → Cookies → https://x.com**.
4. In Firefox, open **Storage → Cookies → https://x.com**.
5. Click inside the cookie grid and copy the whole table to the local clipboard.
6. Do not paste it into Claude, Codex, ChatGPT, GitHub, screenshots, shell arguments, or project files. Tell the local setup agent only **“copied.”**
7. The setup agent runs:

   ```bash
   python scripts/run.py auth-add --clipboard
   ```

   The command extracts only `auth_token` and `ct0`, stores them privately, clears the clipboard, and prints no values.
8. If clipboard access is unavailable, open a separate terminal and run `python scripts/run.py auth-add`; enter only the two values in its hidden prompts.

If cookies are ever pasted into chat or committed to a repository, treat that session as exposed: log out of X to invalidate it, log back in, and use a newly generated session through `auth-add --clipboard` or hidden `auth-add`.

## Verify the installation

```bash
python scripts/run.py doctor --live-auth
python scripts/run.py scan --account lookonchain --hours 6 --limit 5
```

`doctor --live-auth` must report:

- `configured_accounts: 77`, unless the registry was intentionally changed;
- exactly one X session and one active X session;
- `live_auth_verified: true`;
- private runtime and session-database permissions on Unix-like systems.

The scan prints paths to:

- `posts.jsonl` — newly observed raw posts;
- `scan-manifest.json` — reached accounts, failures, duplicate counts, filtering counts, and retry information.

Run the exact one-account scan again. Previously seen post IDs must be counted as duplicates and must not be written again to `posts.jsonl`. If X returns a rate limit, wait until the manifest's `retry_after` time before repeating it.

Run the tests:

```bash
python scripts/run.py test
```

## The everyday command

From the installed skill directory, run:

```bash
python scripts/run.py scan-all --hours 24 --limit 20 --max-runtime-seconds 900
```

This full-registry scan includes every enabled account in bounded batched X search queries and publishes one combined raw JSONL plus a registry manifest. The manifest explicitly reports enabled, queried, and unqueried accounts and never equates a successful query pass with complete X coverage. Do not overlap scans.

Inspect the latest output with:

```bash
python scripts/run.py latest
python scripts/run.py health
```

Useful daily commands:

```bash
# Check authentication and local readiness
python scripts/run.py doctor

# Collect the next account in the round-robin
python scripts/run.py scan --hours 24

# Collect one specific enabled account
python scripts/run.py scan --account lookonchain --hours 24 --limit 20

# Inspect account successes and failures
python scripts/run.py health

# List or validate the registry
python scripts/run.py accounts list
python scripts/run.py accounts validate
```

Avoid `--all-accounts` for ordinary use. It is explicit because X is likely to rate-limit a one-session full-registry attempt.

## Manage monitored accounts

```bash
python scripts/run.py configure --lookback-hours 24 --per-account-limit 20 --post-types quotes
python scripts/run.py accounts add example_user --tier high --tags bitcoin,news
python scripts/run.py accounts import /absolute/path/accounts.csv --mode merge
python scripts/run.py accounts disable example_user
python scripts/run.py accounts enable example_user
python scripts/run.py accounts remove example_user
python scripts/run.py accounts validate
python scripts/run.py auth-remove --yes
```

Registry validation checks username syntax and case-insensitive uniqueness. It does not promise that every handle currently exists on X. Live scans report unavailable handles explicitly and move to the next account.

## Install from the packaged skill

The release artifact is [`dist/crypto-signal-scan.skill`](../dist/crypto-signal-scan.skill). It is a ZIP-compatible Agent Skill package. An Agent Skills-compatible coding agent can extract or copy its `crypto-signal-scan/` directory into the client's documented local skill directory.

Examples vary by client. Claude Code commonly uses:

```text
~/.claude/skills/crypto-signal-scan/
```

The installer agent must detect the client's supported location instead of assuming every client uses the same directory.

## Data and credential locations

Default runtime root:

- Linux: `${XDG_DATA_HOME:-~/.local/share}/crypto-signal-scan`
- macOS and other Unix-like systems: `${XDG_DATA_HOME:-~/.local/share}/crypto-signal-scan`
- Windows: `%LOCALAPPDATA%\crypto-signal-scan`

The runtime contains configuration, the local account registry, SQLite deduplication/account-health state, output files, and the owner-only X session database. It must never be committed to Git.

## Important limitation

The collector uses X's unofficial web interface. It worked in live validation and has explicit handling for rate limits, unavailable accounts, authentication failures, restart persistence, and duplicate suppression. That does not guarantee permanent uptime or authorization from X. X can change the interface or enforce its terms at any time.
