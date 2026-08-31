# Crypto Signal Scan build proof

**Build state:** ready for one user-owned X cookie session and a live one-account scan.

## Implemented

- Valid Agent Skill at `skills/crypto-signal-scan/`.
- Pinned `twscrape==0.20.1` isolated runtime bootstrap.
- Owner-only runtime directory and session/state SQLite permissions.
- Hidden local cookie entry accepting either a cookie header or separate `auth_token` and `ct0` values.
- Bundled registry of 77 unique enabled crypto accounts with tier/tag metadata. Review removed the syntactically impossible 16-character `cryptocoinbuster` handle and corrected `JupiterAggregator` to the documented official `JupiterExchange` handle.
- Add/remove/enable/disable/list/validate account commands.
- Bounded timeline collection with maximum concurrency 4 and default 2.
- Default policy keeps originals and quote posts while excluding replies and reposts.
- Normalized JSONL, manifest, SQLite deduplication, account health, partial-failure reporting, and exact source URLs.
- One-question-at-a-time setup interview and portable installer-agent prompt.
- Packaged artifact at `dist/crypto-signal-scan.skill`.

## Automated evidence

- Nine unit tests pass: registry syntax/count/uniqueness, risk acknowledgement updates, secret parsing and named-cookie entry, one-session replacement, registry mutation, normalized collection plus duplicate suppression, write-boundary allowlisting, and post-type policy.
- Python files compile successfully.
- Fresh runner bootstrap installed the pinned dependency and initialized 77 accounts.
- Packaged artifact extracted into a clean temporary directory and initialized successfully.
- Skill validation passed through the skill-creator packager.
- Gitleaks found no secrets in the skill.
- `pip-audit` found no known vulnerabilities in the resolved pinned requirements.
- Local runtime doctor currently reports 77 accounts, acknowledged risk, mode `0700`, session DB mode `0600`, and zero authenticated X sessions.
- Independent re-review found no remaining static/code blocker before a real cookie-backed one-account scan; all pinned twscrape integration signatures match.
- Three prompt evaluations scored 100% with the skill versus 66.7% without it. The skill advantage came from the one-question setup contract and strict collection-only routing; account-management behavior was non-discriminating. Static review: `skills/crypto-signal-scan-workspace/iteration-1/review.html`.

## Remaining live proof

The server-side Chromium profile has no `auth_token` or `ct0` for X. No credential was fabricated, requested in chat, or copied from another service. A user must enter their own X cookie values through the hidden local command:

```bash
cd /home/drewp/main-projects/jordancrypto/skills/crypto-signal-scan
python scripts/run.py auth-add
```

After that, completion requires:

1. `doctor` reports one local X session.
2. A one-account scan reaches the account and emits attributable raw records.
3. The exact scan is repeated and previously seen post IDs are suppressed.

This proves present functionality on this computer; it cannot prove that X's unofficial interface will never change or that X will permit the activity indefinitely.
