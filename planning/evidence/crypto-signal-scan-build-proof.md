# Crypto Signal Scan build proof

**Build state:** complete and live-validated on 2026-08-31 with one user-owned X cookie session.

## Implemented

- Valid Agent Skill at `skills/crypto-signal-scan/`.
- Pinned `twscrape==0.20.1` isolated runtime bootstrap.
- Owner-only runtime directory and session/state SQLite permissions.
- Hidden local cookie entry accepting either a cookie header or separate `auth_token` and `ct0` values.
- Bundled registry of 77 unique enabled crypto accounts with tier/tag metadata. Review removed the syntactically impossible 16-character `cryptocoinbuster` handle and corrected `JupiterAggregator` to the documented official `JupiterExchange` handle.
- Add/remove/enable/disable/list/validate account commands.
- Bounded timeline collection with maximum concurrency 4 and default 2. Automatic scans use a persistent one-account round-robin; `--all-accounts` is explicit because one session cannot safely exhaust all 77 timelines at once. A cross-process lock rejects overlapping scans and session replacement during scans so the cursor and sole session cannot race.
- Default policy keeps originals and quote posts while excluding replies and reposts.
- Normalized JSONL, manifest, SQLite deduplication, account health, partial-failure reporting, exact source URLs, and structured retryable rate-limit errors with the earliest known UTC retry time.
- One-question-at-a-time setup interview and portable installer-agent prompt.
- Packaged artifact at `dist/crypto-signal-scan.skill`.

## Automated evidence

- Thirty unit tests pass, including non-TTY auth refusal, clipboard-table parsing/clearing, staged pre-auth doctor, configuration, physical session removal, bounded cycle publication, and retry-deadline cursor retention, plus 25 idempotent repeated scans, restart persistence, partial failure, bounded concurrency, persistent round-robin rotation at both helper and CLI levels, no cursor advance during rate limits, retry-vs-unusable-session classification, earliest retry selection, overlapping-process rejection, single-account batch enforcement, registry validation, one-session replacement, exact provider-side limits, write-boundary allowlisting, and post-type policy.
- Python files compile successfully.
- A separate 100-cycle deterministic full-registry soak reached 7,700 account iterations with zero failures: 77 records were new on cycle one and 7,623 duplicates were suppressed on the next 99 cycles.
- Fresh runner bootstrap installed the pinned dependency and initialized 77 accounts.
- Packaged artifact extracted into a clean temporary directory and initialized successfully.
- Skill validation passed through the skill-creator packager.
- Gitleaks found no secrets in the skill.
- `pip-audit` found no known vulnerabilities in the resolved pinned requirements.
- During live proof, `doctor --live-auth` reported 77 accounts, acknowledged risk, mode `0700`, session DB mode `0600`, exactly one active X session, and a successful authenticated lookup.
- Independent re-review found no remaining blocker after the repeatability changes and separately confirmed adapter classification, single-account rotation, cross-process locking, minimum retry selection, manifest schema, one-session enforcement, and package/source parity.
- Three prompt evaluations scored 100% with the skill versus 66.7% without it. The skill advantage came from the one-question setup contract and strict collection-only routing; account-management behavior was non-discriminating. Static review: `skills/crypto-signal-scan-workspace/iteration-1/review.html`.

## Live proof

- A cookie-backed `lookonchain` scan reached one requested account with zero account failures and emitted attributable raw JSONL records using `twscrape-0.20.1`.
- An emitted public URL resolved with HTTP 200 and matched the allowlisted author and post ID.
- The first live call revealed that twscrape may return a full page even when given a smaller limit. The provider now enforces the exact limit itself, and a dedicated unit test covers the behavior.
- The fixed-code repeat processed exactly five returned records: four were existing post IDs, one was filtered by policy, zero were emitted as new, the account was reached, and the JSONL output was empty. This proves duplicate suppression without hiding scan health.
- A later cookie-backed `whale_alert` scan reached the account with zero failures and emitted two new records under an exact three-post request.
- Repeated default live scans proved round-robin behavior: `colin_alex` was reached and advanced cursor 0→1; missing `metrika_co` was reported non-retryable and advanced 1→2; `zhusu` was reached and advanced 2→3; then rate-limited `santimentfeed` reported `retry_after: 2026-09-01T00:48:27+00:00` and correctly held cursor 3→3.
- The improved manifest reports rate limits as `retryable: true` with the exact `retry_after` timestamp, exits nonzero, and does not claim success or lose the next account position.
- The session values used for proof had been exposed in chat. After validation, the local session row was deleted and doctor reports zero stored sessions; the user must log out and create a fresh session before ongoing use, entering it only through the hidden `auth-add` prompt.

This proves present functionality on this computer at the checked time. It cannot prove that X's unofficial interface will never change or that X will permit the activity indefinitely.
