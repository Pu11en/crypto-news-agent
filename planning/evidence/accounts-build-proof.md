# Twitter News Accounts build proof

**Build state:** production-candidate and live-validated on 2026-09-01 with one user-owned X cookie session.

## Implemented

- Valid Agent Skill at `skills/accounts/`.
- Pinned `twscrape==0.20.1` isolated runtime bootstrap.
- Owner-only runtime directory and session/state SQLite permissions.
- Local clipboard cookie-table import that retains only `auth_token`/`ct0`, clears the clipboard, prints no values, and falls back to separate-terminal hidden entry.
- Bundled registry of 77 unique crypto accounts with tier/tag metadata, 76 enabled by default. Review removed the syntactically impossible 16-character `cryptocoinbuster` handle and corrected `JupiterAggregator` to the documented official `JupiterExchange` handle.
- Add/remove/enable/disable/list/validate account commands.
- Default full-registry collection uses bounded one-page search batches and attempts every enabled account; targeted scans remain available and the slow timeline cycle is legacy/explicit. A cross-process lock rejects overlapping scans and session replacement during scans.
- Default policy keeps originals and quote posts while excluding replies and reposts.
- Normalized JSONL, manifest, SQLite deduplication, account health, partial-failure reporting, exact source URLs, and structured retryable rate-limit errors. Output files are flushed and published before seen-post claims commit, so a crash may harmlessly re-emit but cannot permanently hide an unwritten post.
- One-question-at-a-time setup interview and portable installer-agent prompt.
- Packaged artifact at `dist/accounts.skill`.

## Automated evidence

- Forty-five unit tests pass. Coverage includes full/partial registry search, generic provider failure, rate-limit deadlines, restart/dedup behavior, 25 repeated scans, concurrency/locking, registry validation, exact limits, post policy, output-path confinement, corrupt/wrong-type configuration, legacy config compatibility, serialization failure without false dedup claims, history reset preservation, mixed clipboard exports, failure-atomic session replacement, and staged-runtime bootstrap failure/reuse.
- Python files compile successfully.
- A separate 100-cycle deterministic full-registry soak reached 7,700 account iterations with zero failures: 77 records were new on cycle one and 7,623 duplicates were suppressed on the next 99 cycles.
- Fresh runner bootstrap installed the pinned dependency and initialized 77 accounts.
- The packaged artifact passed ZIP CRC plus exact runtime file/content parity against source, then extracted into a clean Claude Code-style `.claude/skills/accounts` directory, built its isolated runtime, passed all 45 tests, initialized, and reached pre-auth doctor readiness even with a hostile inherited `PYTHONPATH`.
- Skill validation passed through the skill-creator packager.
- Gitleaks found no secrets in the skill.
- `pip-audit` found no known vulnerabilities in the resolved pinned requirements.
- The Accounts identity and shared toolkit migration preserved one active verified X session plus saved output under `~/.local/share/twitter-news/accounts`. After the safe history reset removed all outputs and dedup/account-health state while retaining authentication, registry, configuration, and runtime, a genuinely fresh scan completed all 76 enabled accounts with zero unqueried, preserved 87 eligible posts, marked all 87 new with zero previously seen, and reported no pending rate limit. After production hardening, another live full pass again queried 76/76 with zero unqueried, preserved 87 eligible posts, correctly classified 1 new and 86 previously seen, and passed live-auth doctor.
- Three independent production audits identified and drove fixes for destructive reauthentication, non-atomic output/dedup ordering, unhandled provider/bootstrap failures, corrupt configuration and escaped output pointers, inherited Python-path contamination, stale package risk, expired-cookie recovery, Claude Code installation/permissions, output caps, and routing gaps. Unavoidable X endpoint, cookie-expiration, challenge, rate-limit, and bounded-retrieval risks remain explicitly disclosed.
- CI now tests source on Ubuntu Python 3.10/3.12 and Windows 3.12, validates release ZIP integrity and exact source contents, extracts it into a Claude Code-style skill path, and runs packaged tests plus pre-auth initialization/doctor.
- Three early prompt evaluations scored 100% with the skill versus 66.7% without it. Later real-session feedback showed that the collection-only boundary made the feed unusable for creative follow-up, so that restriction was removed while raw outputs remained intact. The obsolete collection-only evaluation artifacts were removed during the product rename.
- Fresh routing evaluations confirmed that “Open Twitter News” displays the toolkit menu without contacting X, clearly marks Accounts available and Barron planned/not installed, while “Run Accounts” skips the menu and defaults to the exact full-registry 24-hour command. Separate evaluations correctly handled headless Claude Code installation, narrow permissions, expired-cookie recovery without history loss, 429/auth distinction, saved-results curation, and source-linked creative output.

## Live proof

- A cookie-backed `lookonchain` scan reached one requested account with zero account failures and emitted attributable raw JSONL records using `twscrape-0.20.1`.
- An emitted public URL resolved with HTTP 200 and matched the allowlisted author and post ID.
- The first live call revealed that twscrape may return a full page even when given a smaller limit. The provider now enforces the exact limit itself, and a dedicated unit test covers the behavior.
- The fixed-code repeat processed exactly five returned records: four were existing post IDs, one was filtered by policy, zero were emitted as new, the account was reached, and the JSONL output was empty. This proves duplicate suppression without hiding scan health.
- A later cookie-backed `whale_alert` scan reached the account with zero failures and emitted two new records under an exact three-post request.
- Repeated default live scans proved round-robin behavior: `colin_alex` was reached and advanced cursor 0→1; missing `metrika_co` was reported non-retryable and advanced 1→2; `zhusu` was reached and advanced 2→3; then rate-limited `santimentfeed` reported `retry_after: 2026-09-01T00:48:27+00:00` and correctly held cursor 3→3.
- The improved manifest reports rate limits as `retryable: true` with the exact `retry_after` timestamp, exits nonzero, and does not claim success or lose the next account position.
- Current proof uses one locally stored, owner-only session; `doctor --live-auth` reports exactly one active session and no cookie values are printed or included in evidence.

This proves present functionality on this computer at the checked time. It cannot prove that X's unofficial interface will never change or that X will permit the activity indefinitely.
