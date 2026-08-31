# Crypto Signal Scan build proof

**Build state:** complete and live-validated on 2026-08-31 with one user-owned X cookie session.

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

- Ten unit tests pass: registry syntax/count/uniqueness, risk acknowledgement updates, secret parsing and named-cookie entry, one-session replacement, registry mutation, normalized collection plus duplicate suppression, exact provider-side result limiting, write-boundary allowlisting, and post-type policy.
- Python files compile successfully.
- Fresh runner bootstrap installed the pinned dependency and initialized 77 accounts.
- Packaged artifact extracted into a clean temporary directory and initialized successfully.
- Skill validation passed through the skill-creator packager.
- Gitleaks found no secrets in the skill.
- `pip-audit` found no known vulnerabilities in the resolved pinned requirements.
- During live proof, `doctor --live-auth` reported 77 accounts, acknowledged risk, mode `0700`, session DB mode `0600`, exactly one active X session, and a successful authenticated lookup.
- Independent re-review found no remaining static/code blocker before a real cookie-backed one-account scan; all pinned twscrape integration signatures match.
- Three prompt evaluations scored 100% with the skill versus 66.7% without it. The skill advantage came from the one-question setup contract and strict collection-only routing; account-management behavior was non-discriminating. Static review: `skills/crypto-signal-scan-workspace/iteration-1/review.html`.

## Live proof

- A cookie-backed `lookonchain` scan reached one requested account with zero account failures and emitted attributable raw JSONL records using `twscrape-0.20.1`.
- An emitted public URL resolved with HTTP 200 and matched the allowlisted author and post ID.
- The first live call revealed that twscrape may return a full page even when given a smaller limit. The provider now enforces the exact limit itself, and a dedicated unit test covers the behavior.
- The fixed-code repeat processed exactly five returned records: four were existing post IDs, one was filtered by policy, zero were emitted as new, the account was reached, and the JSONL output was empty. This proves duplicate suppression without hiding scan health.
- An immediate second-account attempt encountered the expected per-endpoint X timeline lock. The collector reported one failed account and exited nonzero rather than claiming success.
- The session values used for proof had been exposed in chat. After validation, the local session row was deleted; zero sessions remain stored. The user must log out and create a fresh session before ongoing use, entering it only through the hidden `auth-add` prompt.

This proves present functionality on this computer at the checked time. It cannot prove that X's unofficial interface will never change or that X will permit the activity indefinitely.
