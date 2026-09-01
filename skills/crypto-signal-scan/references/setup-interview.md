# Setup interview

Use only when no valid local configuration exists. Ask exactly one multiple-choice question per turn, put recommended `A.` first, explain each choice briefly, and never request credentials in chat.

1. **Risk acceptance** — explain the unofficial X interface and terms/enforcement risk before initialization; stop if declined.
2. **Account source** — A. bundled plus additions; B. bundled unchanged; C. custom-only CSV.
3. **Post types** — A. originals plus quotes (`quotes`); B. originals only (`originals`); C. include replies/reposts (`all`).
4. **Lookback** — A. 24 hours; B. 12; C. 6; D. custom positive integer.
5. **Per-account fetch cap** — A. 20; B. 50; C. 10; D. custom 1–200.

Apply choices with supported commands, never by editing runtime internals:

```bash
python scripts/run.py configure --lookback-hours 24 --per-account-limit 20 --post-types quotes
python scripts/run.py accounts import /absolute/path/accounts.csv --mode merge
# custom-only, only after explicit confirmation:
python scripts/run.py accounts import /absolute/path/accounts.csv --mode replace
```

Then run pre-auth doctor, hand cookie entry to a separate interactive terminal, run live-auth doctor, perform a live scan and duplicate proof, run tests, and show the `cycle`, `latest`, and `health` commands.
