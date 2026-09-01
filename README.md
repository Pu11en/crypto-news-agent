# Twitter News

A small, portable Agent Skills toolkit for collecting public X posts with one user-owned browser session and using the saved feed for summaries, rankings, rundowns, scripts, and other creative work.

No paid X API is required.

## Available skills

- **`accounts` — available:** collects the previous 24 hours from every enabled account in the bundled crypto registry.
- **`barron` — planned, not installed:** future 12-hour finance and memecoin coverage.

Say **“Open Twitter News”** to display the menu. Say **“Run Accounts”** to run the available skill directly.

## Give this repository to Claude Code

Open an empty folder in Claude Code and paste:

```text
Install and fully validate the Twitter News Accounts skill from:
https://github.com/Pu11en/crypto-news-agent

Clone the repository, then read skills/accounts/references/installer-agent-prompt.md and follow it exactly. Detect my operating system and Python command instead of assuming them. Guide me one question at a time. Never ask me to paste X cookies into chat; use the local clipboard flow after I reply only “copied,” with a separate interactive hidden terminal as fallback. Do not declare success until the installed skill is verified, doctor --live-auth passes, a live proof and duplicate check succeed, and all bundled tests pass. When finished, tell me to start a fresh Claude Code session and say “Open Twitter News.”
```

The complete installer procedure is in [`skills/accounts/references/installer-agent-prompt.md`](skills/accounts/references/installer-agent-prompt.md).

## Direct Claude Code installation

From a cloned checkout:

```bash
mkdir -p ~/.claude/skills
python -m zipfile -e dist/accounts.skill ~/.claude/skills
test -s ~/.claude/skills/accounts/SKILL.md
```

Use `python3` instead when that is the command providing Python 3.10+. Start a fresh Claude Code session after installation.

## Everyday use

```text
Open Twitter News.
Run Accounts.
Use my latest Accounts scrape and give me the top stories with source links.
```

The default Accounts run attempts every enabled monitored account over the previous 24 hours. It preserves all eligible current-run posts, identifies globally new posts, reports queried and unqueried accounts, and keeps exact X links.

## Repository contents

```text
.github/workflows/accounts.yml     Cross-platform release tests
dist/accounts.skill                Installable Agent Skill package
docs/TWITTER_NEWS.md               Complete user guide
skills/accounts/                   Skill, collector, tests, registry, references
```

## Requirements and limitations

- Python 3.10 or newer
- A normal browser login to an X account the user controls
- Local terminal access
- Direct networking; no proxy or identity rotation

The collector uses X's unofficial web interface. X may change it, expire cookies, challenge sessions, rate-limit requests, or return incomplete bounded results. The skill detects and reports these conditions but cannot guarantee permanent X availability or authorization.
