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

Contains `scan_id`, start/end timestamps, requested/reached/failed accounts, new/duplicate/filtered post counts, lookback hours, backend version, output path, and structured per-account errors. A partial scan is explicit and exits nonzero only when no configured account succeeds or authentication is unusable.

## Local SQLite

SQLite stores seen post IDs plus per-account last-success and error health. The JSON manifest stores scan-level metadata. SQLite is state, not an export, and contains no post ranking or generated interpretation.
