# Setup interview

Use this only when no valid local configuration exists.

## Conversation rules

- Ask exactly one question at a time.
- Put the recommended answer first as `A.`
- Offer only materially different choices, labeled consecutively from `A.` through at most `G.`.
- Explain each choice in one short plain-language line.
- End with a custom-answer path and the valid reply letters.
- Skip questions already answered or made irrelevant by earlier answers.
- Derive installation mechanics yourself; do not ask the user to choose libraries, databases, or internal architecture.
- Never request cookie values, passwords, tokens, or proxy credentials in chat. Credential entry happens later through the invisible local `auth-add` prompt.

## Decision order

1. **Account source** — bundled 77 accounts, custom-only, or bundled plus edits. Recommend bundled plus edits; implement custom choices through the account commands.
2. **Post types** — original posts only; originals plus quotes; or include replies/reposts. Recommend originals plus quotes and write the selected booleans to local config.
3. **Lookback** — 6, 12, 24, or custom hours. Recommend 24 hours and write it to `lookback_hours` in local config.
4. **Per-account limit** — 5, 10, 20, or custom. Recommend 20 for the first scan and write it to `per_account_limit`.
5. **Risk acknowledgement** — explain that free collection uses an unofficial X web interface, can break, and may be enforced against under X's terms. Ask whether to continue before installation.

The MVP always runs on demand, uses direct stable networking, preserves media links without downloading files, emits JSONL plus a manifest, and uses SQLite only for local deduplication and health. Do not offer scheduling, proxy setup, media downloading, CSV export, or retention automation as implemented features.

## Completion

Summarize the selected behavior without credentials, initialize with `init --acknowledge-x-terms-risk`, apply the selected local configuration and account edits, and run `doctor`. Then guide the user to enter cookies locally with `auth-add`, run `doctor --live-auth`, and perform a one-account test scan before scanning the full list.
