# crypto-intel-pipeline-v2

A [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin that
fetches tweets from ~80 crypto Twitter accounts, scores them with
**DeepSeek V4 Pro** via [b.ai](https://docs.b.ai), and surfaces the
results to you on Telegram.

## Architecture

```
Telegram ⇆ Hermes Agent  (single process, multi-threaded)
              ├── platforms/telegram    (bundled)
              ├── model-providers/bai  (our plugin: DeepSeekProfile + b.ai base_url)
              └── crypto-intel         (our pipeline plugin)
                    ├─ run_pipeline, get_digest, get_alerts,
                    │  get_ideas, search_tweets, get_stats
                    ├─ fetcher (Xquik REST: /x/tweets/search?from:user)
                    ├─ scorer  (b.ai batched, 20 tweets/call, thinking disabled)
                    └─ db      (SQLite WAL, thread-safe, /data/crypto-intel/pipeline.db)
```

## Key design decisions

| Decision | Choice | Why |
|----------|--------|-----|
| LLM | DeepSeek V4 Pro via b.ai | Cheap, fast, 1M context, supports tool calling |
| Reasoning | **disabled** | V4 thinking mode returns `reasoning_content` which breaks multi-turn tool routing on b.ai (HTTP 400) |
| Scoring | Batched 20 tweets/prompt | ~75 LLM calls per 1500-tweet run vs 1500 naive |
| DB | SQLite WAL + `check_same_thread=False` | Single-process multi-threaded; per-call short sessions |
| Pipeline trigger | Manual `run_pipeline` in a background thread | Avoids Telegram handler timeouts |

## Local dev

```bash
# 1. Clone Hermes Agent as the base runtime
git clone https://github.com/NousResearch/hermes-agent.git /tmp/hermes
cd /tmp/hermes && docker build -t hermes-base .

# 2. Build this project on top
cd /path/to/crypto-intel-pipeline-v2
docker build -t crypto-intel --build-arg HERMES_BASE=hermes-base .

# 3. Run with secrets
docker run -it --rm \
  -e BAI_API_KEY=sk-... \
  -e XQUIK_API_KEY=xq_... \
  -e TELEGRAM_BOT_TOKEN=... \
  -e TELEGRAM_ALLOWED_USERS=123456789 \
  -e TELEGRAM_HOME_CHANNEL=-1001234567890 \
  -v $(pwd)/data:/data \
  crypto-intel
```

## Deploy to Railway

1. Push this repo to GitHub (done: `Pu11en/crypto-intel-pipeline-v2`).
2. Create a new Railway project → "Deploy from GitHub" → select repo.
3. Add a volume mounted at `/data`.
4. Set env vars in the Railway dashboard (see `railway.toml`).
5. Deploy. Watch logs for "Telegram bot started" + "Plugin crypto-intel loaded".

## Telegram usage

```
/start                          → "Crypto Intel ready"
Run the pipeline                → triggers crypto_run_pipeline
Get digest                      → top tweets min_score 0.6
Get alerts                      → critical-risk only
Get ideas                       → high-score trading hooks
Search "etf flow"               → matches text + summary
Stats                           → last run + DB counts
```

## Files

```
plugins/
  model-providers/bai/   — b.ai provider (subclasses DeepSeekProfile)
  crypto-intel/          — pipeline plugin (8 files, 0 deps outside stdlib + requests + sqlalchemy)
config/
  config.yaml            — pre-seeded Hermes config
Dockerfile               — overlays plugins on the base Hermes image
railway.toml             — Railway service config
```

## Notes

- The accounts list at `plugins/crypto-intel/accounts.txt` is copied
  from `scryptcut/product/accounts.txt` (78 accounts across 10 tiers).
- `seed_accounts()` is idempotent and runs on first `run_pipeline` call.
- `run_pipeline` returns immediately with a run id; the actual work
  happens in a daemon thread. Use `crypto_get_stats` to poll progress.
- The DB lives on the Railway volume at `/data/crypto-intel/pipeline.db`
  and survives redeploys.
