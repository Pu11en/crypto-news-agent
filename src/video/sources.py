from __future__ import annotations

import db


def build_source_bundle(script_id: int) -> dict:
    """Build the only factual package the video director may use."""
    factory = db.session()
    with factory() as s:
        script = s.get(db.Script, script_id)
        if script is None:
            raise ValueError(f"Script {script_id} not found")
        stories = []
        ordered_tweet_ids: list[str] = []
        for story_id in script.story_ids or []:
            story = s.get(db.Story, story_id)
            if story is None:
                continue
            tweet_ids = [str(value) for value in (story.tweet_ids or [])]
            stories.append(
                {
                    "storyId": story.id,
                    "headline": story.headline,
                    "summary": story.summary,
                    "tweetIds": tweet_ids,
                }
            )
            for tweet_id in tweet_ids:
                if tweet_id not in ordered_tweet_ids:
                    ordered_tweet_ids.append(tweet_id)

        rows = (
            s.query(db.Tweet)
            .filter(db.Tweet.tweet_id.in_(ordered_tweet_ids))
            .all()
            if ordered_tweet_ids
            else []
        )
        by_id = {row.tweet_id: row for row in rows}
        tweets = []
        for tweet_id in ordered_tweet_ids:
            row = by_id.get(tweet_id)
            if row is None:
                continue
            tweets.append(
                {
                    "tweetId": row.tweet_id,
                    "username": row.username,
                    "text": row.text,
                    "url": row.url,
                    "createdAt": row.created_at.isoformat() if row.created_at else None,
                }
            )
        return {
            "scriptId": script.id,
            "approvedScript": script.body,
            "stories": stories,
            "tweets": tweets,
        }