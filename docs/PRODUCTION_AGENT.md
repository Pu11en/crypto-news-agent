# Production agent contract

Production runs with `BOT_MODE=scraper`. Local video experiments run with
`BOT_MODE=full`. The two modes share scraping and storage, but they expose
different Telegram capabilities.

## X/Twitter source boundary

- Every new X/Twitter scrape uses the Xquik API.
- The only eligible source accounts are the usernames committed in `accounts.txt`.
- The runtime passes that list into the Xquik client as an allowlist; attempts to
  fetch any other username fail before a network request is made.
- Broad X search, direct X/Twitter API access, browser scraping, alternate account
  lists, and unlisted accounts are not permitted for this agent.

## What production can do

- Ask before spending Xquik credits on every new scrape.
- Reopen the latest saved scrape without scraping again.
- Preserve every scrape, its UTC run time, every collected post, curated
  stories, and original X source links.
- Show curated story research directly in Telegram.
- Generate a complete clickable PDF containing the story index, story evidence,
  and every raw collected post.
- Reuse a previously generated PDF after a restart.
- Browse saved scrapes and page through their raw posts.
- Answer questions grounded in the latest saved scrape.
- Draft or revise a script when the user explicitly requests that help in chat.

## What production cannot do

- Start a new scrape merely because the user sent `/news`.
- Automatically generate a script after scraping.
- Register `/done`, video uploads, caption choices, storyboards, rendering, or
  video-delivery callbacks.
- Create or edit a video.
- Use information outside the saved scrape as if it came from that scrape.
- Expose another Telegram user's saved scrape or report.

## Production flows

### Ask for fresh crypto news

1. The bot explains that a new scrape uses Xquik credits and asks for a natural
   language yes/no confirmation.
2. A clear yes starts the scrape; a clear no cancels it. Any other response asks
   the user to clarify without spending credits.
3. A successful scrape is saved before it is presented.
4. Telegram receives the curated research and source links.
5. The bot generates, validates, caches, and sends the complete PDF.
6. If PDF generation or upload fails, the scrape remains saved and can be
   requested again without triggering another scrape.

### Ask to show saved scrapes

The bot lists saved runs newest first. The user can naturally ask to open a
numbered scrape, view its raw posts, or return to the latest research.

### Natural-language chat

The assistant receives the latest saved scrape as grounded context. It can
explain stories, compare sources, and help draft or revise a script only when
asked. This conversation does not start a scrape or enter the video pipeline.
Production registers no slash-command handlers and clears Telegram's command
menu at startup.

## Reliability guarantees

- SQLite and reports live on the Railway `/data` volume.
- Scrapes are committed before report work begins.
- Report paths are isolated by Telegram user ID and scrape run ID.
- Report writes use a unique temporary file and atomic replacement.
- Cached reports are validated before reuse; corrupt files are regenerated.
- Validation checks page content, run identity, raw-post completeness, and all
  expected source-link annotations.
- Concurrent requests for the same report share a per-run process lock.
- Telegram upload size is checked before upload.
- Report errors are logged and shown as retryable; they do not delete, replace,
  or repeat the scrape.

## Local full-mode flow

`BOT_MODE=full` retains story selection, talking-note generation and refinement,
`/done`, recording upload, optional captions, storyboard review, rendering, and
video delivery. Production does not register those routes.
