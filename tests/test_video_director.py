from __future__ import annotations

from video import align, director, quality


class FakeDirectorLLM:
    def structured(self, system, user_prompt, **kwargs):
        return {
            "scenes": [
                {
                    "beatId": "beat-01",
                    "startSec": 99,
                    "endSec": 100,
                    "communicationGoal": "Explain the first-day inflow",
                    "spokenClaim": "The ETF recorded $42 million.",
                    "viewerTakeaway": "Traditional access arrived with demand.",
                    "evidenceTweetIds": ["tweet-42"],
                    "dataPoints": [{"label": "First day", "value": "$42 million"}],
                    "visualForm": "hero-stat",
                    "kicker": "FIRST DAY",
                    "title": "$42M",
                    "body": "Traditional accounts can now access SOL.",
                    "primaryElements": ["large number", "brokerage pathway"],
                    "annotationPlan": ["circle the number"],
                    "motionPlan": ["draw circle after title"],
                    "densityTarget": 0.85,
                }
            ],
            "composition": {
                "cameraZone": {"x": 100, "y": 100, "width": 1, "height": 1}
            },
        }


def _bundle():
    return {
        "scriptId": 1,
        "approvedScript": "The ETF recorded $42 million.",
        "stories": [
            {
                "storyId": 1,
                "headline": "ETF opens access",
                "summary": "The product recorded $42 million.",
                "tweetIds": ["tweet-42"],
            }
        ],
        "tweets": [
            {
                "tweetId": "tweet-42",
                "username": "sourceacct",
                "text": "ETF inflows reached $42 million in the first day.",
                "url": "https://x.com/sourceacct/status/tweet-42",
            }
        ],
    }


def test_segment_words_covers_the_complete_video_without_gaps():
    words = [
        {"text": "The", "start": 0.2, "end": 0.4},
        {"text": "ETF", "start": 0.4, "end": 0.7},
        {"text": "arrived.", "start": 0.7, "end": 1.1},
        {"text": "Demand", "start": 1.8, "end": 2.2},
        {"text": "followed.", "start": 2.2, "end": 2.8},
    ]

    beats = align.segment_words(words, duration=3.0, target_seconds=1.4)

    assert beats[0]["startSec"] == 0.0
    assert beats[-1]["endSec"] == 3.0
    assert all(left["endSec"] == right["startSec"] for left, right in zip(beats, beats[1:]))


def test_director_enforces_locked_geometry_and_transcript_timing():
    words = [
        {"text": "The", "start": 0.0, "end": 0.3},
        {"text": "ETF", "start": 0.3, "end": 0.6},
        {"text": "recorded", "start": 0.6, "end": 1.0},
        {"text": "$42", "start": 1.0, "end": 1.3},
        {"text": "million.", "start": 1.3, "end": 2.0},
    ]

    storyboard = director.build_storyboard(
        FakeDirectorLLM(), _bundle(), words, duration=2.0, captions_enabled=False
    )

    assert storyboard["composition"]["graphicsZone"] == {
        "x": 0,
        "y": 0,
        "width": 1080,
        "height": 1312,
    }
    assert storyboard["composition"]["cameraZone"] == {
        "x": 0,
        "y": 1312,
        "width": 1080,
        "height": 608,
    }
    assert storyboard["scenes"][0]["startSec"] == 0.0
    assert storyboard["scenes"][0]["endSec"] == 2.0
    assert quality.validate_storyboard(storyboard, _bundle()) == []


def test_director_falls_back_when_model_returns_invalid_output():
    class BrokenLLM:
        def structured(self, *args, **kwargs):
            raise RuntimeError("invalid JSON")

    words = [
        {"text": "The", "start": 0.0, "end": 0.3},
        {"text": "ETF", "start": 0.3, "end": 0.6},
        {"text": "arrived.", "start": 0.6, "end": 1.0},
    ]

    storyboard = director.build_storyboard(
        BrokenLLM(), _bundle(), words, duration=1.0, captions_enabled=False
    )

    assert storyboard["scenes"][0]["visualForm"] == "editorial-statement"
    assert storyboard["scenes"][0]["evidenceTweetIds"] == ["tweet-42"]
