from __future__ import annotations

import db
from video import grounding, sources


def _seed_source_graph() -> int:
    factory = db.session()
    with factory() as s:
        tweet = db.Tweet(
            tweet_id="tweet-42",
            username="sourceacct",
            text="ETF inflows reached $42 million in the first day.",
            url="https://x.com/sourceacct/status/tweet-42",
            tier="high",
            tags="etf|solana",
            run_id="run-source",
        )
        s.add(tweet)
        story = db.Story(
            run_id="run-source",
            rank=1,
            headline="ETF opens access",
            summary="The product recorded $42 million in first-day inflows.",
            tweet_ids=["tweet-42"],
            score=0.95,
        )
        s.add(story)
        s.flush()
        script = db.Script(
            session_id="7",
            version=1,
            body="The ETF recorded $42 million in first-day inflows.",
            story_ids=[story.id],
            is_final=True,
        )
        s.add(script)
        s.commit()
        return script.id


def test_source_bundle_contains_exact_scraped_tweets(isolated_db):
    script_id = _seed_source_graph()

    bundle = sources.build_source_bundle(script_id)

    assert bundle["approvedScript"].startswith("The ETF")
    assert bundle["stories"][0]["tweetIds"] == ["tweet-42"]
    assert bundle["tweets"][0]["text"] == "ETF inflows reached $42 million in the first day."
    assert bundle["tweets"][0]["url"].endswith("tweet-42")


def test_grounding_rejects_numbers_and_evidence_not_in_scraper(isolated_db):
    script_id = _seed_source_graph()
    bundle = sources.build_source_bundle(script_id)
    valid = {
        "scenes": [
            {
                "id": "scene-01",
                "spokenClaim": "The ETF recorded $42 million in inflows.",
                "evidenceTweetIds": ["tweet-42"],
                "dataPoints": [{"label": "First day", "value": "$42 million"}],
                "visualForm": "hero-stat",
            }
        ]
    }
    invalid = {
        "scenes": [
            {
                "id": "scene-01",
                "spokenClaim": "The ETF recorded $99 million in inflows.",
                "evidenceTweetIds": ["tweet-missing"],
                "dataPoints": [{"label": "First day", "value": "$99 million"}],
                "visualForm": "bar-chart",
            }
        ]
    }

    assert grounding.validate_storyboard(valid, bundle) == []
    errors = grounding.validate_storyboard(invalid, bundle)

    assert any("tweet-missing" in error for error in errors)
    assert any("99" in error for error in errors)


def test_number_in_script_but_not_scraped_tweet_is_unsupported(isolated_db):
    script_id = _seed_source_graph()
    bundle = sources.build_source_bundle(script_id)
    bundle["approvedScript"] = "The ETF recorded $77 million."
    storyboard = {
        "scenes": [{
            "id": "scene-script-only",
            "spokenClaim": "The ETF recorded $77 million.",
            "evidenceTweetIds": ["tweet-42"],
            "dataPoints": [{"label": "Inflow", "value": "$77 million"}],
            "visualForm": "hero-stat",
        }]
    }

    errors = grounding.validate_storyboard(storyboard, bundle)

    assert any("77" in error for error in errors)


def test_number_hidden_in_rendered_title_is_rejected(isolated_db):
    bundle = sources.build_source_bundle(_seed_source_graph())
    storyboard = {
        "scenes": [{
            "id": "scene-title",
            "spokenClaim": "ETF inflows increased.",
            "title": "$88M INFLOW",
            "body": "The first day changed access.",
            "evidenceTweetIds": ["tweet-42"],
            "dataPoints": [],
            "visualForm": "editorial-statement",
        }]
    }

    errors = grounding.validate_storyboard(storyboard, bundle)

    assert any("88" in error for error in errors)


def test_claim_audit_requires_supported_status_and_exact_scraper_quote(isolated_db):
    bundle = sources.build_source_bundle(_seed_source_graph())
    storyboard = {
        "scenes": [{
            "id": "scene-01",
            "spokenClaim": "The ETF recorded $42 million in first-day inflows.",
        }]
    }
    valid = {
        "claims": [{
            "sceneId": "scene-01",
            "status": "supported",
            "evidence": [{
                "tweetId": "tweet-42",
                "quote": "ETF inflows reached $42 million in the first day.",
            }],
        }]
    }
    invalid = {
        "claims": [{
            "sceneId": "scene-01",
            "status": "supported",
            "evidence": [{
                "tweetId": "tweet-42",
                "quote": "This quote was never scraped.",
            }],
        }]
    }

    assert grounding.validate_claim_audit(storyboard, bundle, valid) == []
    errors = grounding.validate_claim_audit(storyboard, bundle, invalid)
    assert any("exact scraper quote" in error for error in errors)
