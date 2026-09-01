# Output contract

## `posts.jsonl`

One JSON object per newly observed public post:

```json
{
  "post_id": "1234567890",
  "author_id": "2244994945",
  "username": "xdevelopers",
  "text": "Post text",
  "created_at": "2026-08-31T12:00:00+00:00",
  "public_url": "https://x.com/xdevelopers/status/1234567890",
  "in_reply_to_post_id": null,
  "quoted_post_id": null,
  "reposted_post_id": null,
  "media_urls": [],
  "external_links": [],
  "fetched_at": "2026-08-31T12:01:00+00:00",
  "source_adapter": "twscrape-0.20.1",
  "scan_id": "uuid"
}
```

IDs are strings. Timestamps are UTC ISO 8601. Missing values are `null`; collections are empty arrays.

## `scan-manifest.json`

Contains `scan_id`, start/end timestamps, requested/reached/failed accounts, new/duplicate/filtered post counts, lookback hours, backend version, output path, and structured per-account errors. Each failed-account object has string `username` and `error`, booleans `retryable` and `fatal`, and nullable UTC ISO 8601 string `retry_after`. Only a confirmed active endpoint lock is retryable; an unusable session is fatal and directs the operator to `doctor --live-auth`. Manifests also report `fetched_posts`, `post_processing_errors`, `bounded_by_request_limit`, and a best-effort `coverage_scope`; reaching an account is not a claim of complete timeline coverage.

Automatic scans also contain `account_rotation`:

```json
{
  "mode": "round_robin",
  "cursor_before": 3,
  "cursor_after": 3,
  "registry_size": 77,
  "advanced": false
}
```

The cursor advances after success or a non-retryable account error. It remains fixed during a retryable endpoint lock. Explicit `--account` and `--all-accounts` scans omit this object. A partial scan is explicit and exits nonzero only when no requested account succeeds or authentication is unusable.

## Local SQLite

SQLite stores seen post IDs, per-account last-success/error health, and the persistent account-rotation cursor. The JSON manifest stores scan-level metadata, including `account_rotation` for automatic round-robin runs. SQLite is state, not an export, and contains no post ranking or generated interpretation.

## Full-registry output

`scan-all` writes `output/registry-scans/<id>/combined.jsonl` and `registry-manifest.json`, then atomically updates `output/latest.json`. Its manifest reports `enabled_accounts`, `queried_accounts`, `queried_usernames`, `unqueried_usernames`, `full_registry_pass`, per-batch results, retry time, and bounded best-effort completeness wording. `full_registry_pass` means every enabled handle appeared in a successful X search query; it does not claim X returned every post.

## Legacy cycle output

`cycle` writes immutable `output/cycles/<id>/combined.jsonl` and `cycle-manifest.json`, then atomically updates `output/latest.json`. The cycle manifest records status, stop reason, cursor movement, per-account round manifests, bounded coverage wording, and totals. `latest` prints the stable pointer; deadline-limited cycles are valid partial outputs and resume from the retained cursor.
