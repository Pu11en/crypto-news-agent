"""Unit tests for the default-deny curation vetting (wayfinder map #2 / build #6).

These cover `_vet_stories` directly: each deterministic cut path (ungrounded /
low_score / over_cap), first-cut-wins ordering, the all-pass case, and the
observability summary. The vetting function is pure, so no DB is needed.
"""

from __future__ import annotations

from datetime import datetime, timezone

from handlers import news
from xquik import RawTweet


def _tweet(tweet_id: str) -> RawTweet:
    return RawTweet(
        tweet_id=tweet_id,
        username="acct",
        text="body",
        created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        url=None,
    )


def _story(rank: int, tweet_ids: list[str], score: float, headline: str = "h") -> dict:
    return {
        "rank": rank,
        "headline": headline,
        "summary": "s",
        "score": score,
        "tweet_ids": tweet_ids,
    }


def test_vet_stories_displays_all_when_everything_clears(isolated_db):
    tweets = [_tweet("t1"), _tweet("t2")]
    stories = [
        _story(1, ["t1"], 0.95),
        _story(2, ["t2"], 0.8),
    ]

    out = news._vet_stories(stories, tweets, max_stories=5)

    assert len(out) == 2
    assert all(s["display_ok"] for s in out)
    assert all(s["cut_reason"] is None for s in out)


def test_vet_stories_cuts_ungrounded_stories(isolated_db):
    # A story whose tweet_ids don't intersect the scraped set is ungrounded,
    # even with a high score — first-cut-wins means grounding runs before score.
    tweets = [_tweet("t1")]
    stories = [
        _story(1, ["t1"], 0.95, "grounded"),
        _story(2, ["made-up"], 0.99, "invented"),
        _story(3, [], 0.9, "no sources at all"),
    ]

    out = news._vet_stories(stories, tweets, max_stories=5)

    by_headline = {s["headline"]: s for s in out}
    assert by_headline["grounded"]["display_ok"] is True
    assert by_headline["invented"]["display_ok"] is False
    assert by_headline["invented"]["cut_reason"] == "ungrounded"
    assert by_headline["no sources at all"]["display_ok"] is False
    assert by_headline["no sources at all"]["cut_reason"] == "ungrounded"


def test_vet_stories_cuts_below_score_floor(isolated_db):
    tweets = [_tweet("t1"), _tweet("t2")]
    stories = [
        _story(1, ["t1"], 0.72, "above floor"),
        _story(2, ["t2"], 0.7, "exactly at floor"),
        _story(3, ["t2"], 0.69, "below floor"),
    ]

    out = news._vet_stories(stories, tweets, max_stories=5)

    by_headline = {s["headline"]: s for s in out}
    assert by_headline["above floor"]["display_ok"] is True
    assert by_headline["exactly at floor"]["display_ok"] is True
    assert by_headline["below floor"]["display_ok"] is False
    assert by_headline["below floor"]["cut_reason"] == "low_score"


def test_vet_stories_applies_hard_cap_to_lowest_survivors(isolated_db):
    # Six grounded, above-floor stories but max_stories=3: the three lowest-
    # scoring survivors are cut as over_cap, never the top scorers.
    tweets = [_tweet(f"t{i}") for i in range(1, 7)]
    stories = [_story(rank, [f"t{rank}"], score, f"story {rank}") for rank, score in
               [(1, 0.95), (2, 0.9), (3, 0.85), (4, 0.8), (5, 0.78), (6, 0.75)]]

    out = news._vet_stories(stories, tweets, max_stories=3)

    displayed = sorted(out, key=lambda s: float(s["score"]), reverse=True)
    kept = [s for s in displayed if s["display_ok"]]
    cut = [s for s in displayed if not s["display_ok"]]

    assert len(kept) == 3
    assert len(cut) == 3
    # The three highest scores survive; the bottom three are over_cap.
    assert {s["headline"] for s in kept} == {"story 1", "story 2", "story 3"}
    assert all(s["cut_reason"] == "over_cap" for s in cut)
    assert {s["headline"] for s in cut} == {"story 4", "story 5", "story 6"}


def test_vet_stories_first_cut_wins_grounding_before_score(isolated_db):
    # An ungrounded story with a low score is recorded as ungrounded, not
    # low_score: grounding is checked first.
    tweets = [_tweet("t1")]
    stories = [_story(1, ["missing"], 0.1)]

    out = news._vet_stories(stories, tweets, max_stories=5)

    assert out[0]["display_ok"] is False
    assert out[0]["cut_reason"] == "ungrounded"


def test_vet_stories_handles_missing_or_bad_scores(isolated_db):
    tweets = [_tweet("t1"), _tweet("t2"), _tweet("t3")]
    stories = [
        {"rank": 1, "headline": "no score key", "summary": "s", "tweet_ids": ["t1"]},
        {"rank": 2, "headline": "non-numeric", "summary": "s", "score": "high", "tweet_ids": ["t2"]},
        {"rank": 3, "headline": "valid", "summary": "s", "score": 0.8, "tweet_ids": ["t3"]},
    ]

    out = news._vet_stories(stories, tweets, max_stories=5)

    by_headline = {s["headline"]: s for s in out}
    assert by_headline["no score key"]["display_ok"] is False
    assert by_headline["no score key"]["cut_reason"] == "low_score"
    assert by_headline["non-numeric"]["display_ok"] is False
    assert by_headline["non-numeric"]["cut_reason"] == "low_score"
    assert by_headline["valid"]["display_ok"] is True


def test_vet_stories_preserves_full_list_for_audit_trail(isolated_db):
    # Cut stories are retained in the returned list so they can be persisted
    # with their cut_reason for later inspection.
    tweets = [_tweet("t1")]
    stories = [
        _story(1, ["t1"], 0.9, "kept"),
        _story(2, ["ghost"], 0.95, "cut"),
    ]

    out = news._vet_stories(stories, tweets, max_stories=5)

    assert len(out) == 2  # nothing dropped from the list
    headlines = [s["headline"] for s in out]
    assert "kept" in headlines and "cut" in headlines


def test_summarise_cuts_groups_by_reason(isolated_db):
    stories = [
        {"display_ok": True},
        {"display_ok": True},
        {"display_ok": False, "cut_reason": "ungrounded"},
        {"display_ok": False, "cut_reason": "ungrounded"},
        {"display_ok": False, "cut_reason": "low_score"},
        {"display_ok": False, "cut_reason": "over_cap"},
    ]

    summary = news._summarise_cuts(stories)

    assert summary == {
        "returned": 6,
        "displayed": 2,
        "cuts": {"ungrounded": 2, "low_score": 1, "over_cap": 1},
    }


def test_summarise_cuts_handles_clean_run(isolated_db):
    summary = news._summarise_cuts([{"display_ok": True}, {"display_ok": True}])
    assert summary == {"returned": 2, "displayed": 2, "cuts": {}}
