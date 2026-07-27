from __future__ import annotations

import json
import re

_NUMBER = re.compile(r"(?<![A-Za-z])[-+]?\d[\d,]*(?:\.\d+)?%?")


def _numbers(value: str) -> set[str]:
    return {match.replace(",", "") for match in _NUMBER.findall(value or "")}


def validate_storyboard(storyboard: dict, source_bundle: dict) -> list[str]:
    """Reject evidence IDs and exact numbers absent from the scraper package."""
    errors: list[str] = []
    tweets = source_bundle.get("tweets", [])
    valid_tweet_ids = {str(tweet.get("tweetId")) for tweet in tweets}
    source_text = "\n".join(
        [source_bundle.get("approvedScript", "")]
        + [str(tweet.get("text", "")) for tweet in tweets]
        + [str(story.get("summary", "")) for story in source_bundle.get("stories", [])]
    )
    grounded_numbers = _numbers(source_text)

    for scene in storyboard.get("scenes", []):
        scene_id = str(scene.get("id", "unknown scene"))
        evidence_ids = [str(value) for value in scene.get("evidenceTweetIds", [])]
        for tweet_id in evidence_ids:
            if tweet_id not in valid_tweet_ids:
                errors.append(f"{scene_id}: unknown evidence tweet {tweet_id}")

        factual_payload = " ".join(
            [
                str(scene.get("spokenClaim", "")),
                json.dumps(scene.get("dataPoints", []), ensure_ascii=False),
            ]
        )
        for number in sorted(_numbers(factual_payload)):
            if number not in grounded_numbers:
                errors.append(f"{scene_id}: number {number} is not in the scraper source package")

        visual_form = str(scene.get("visualForm", ""))
        if "chart" in visual_form and not scene.get("dataPoints"):
            errors.append(f"{scene_id}: chart requested without grounded data points")
    return errors