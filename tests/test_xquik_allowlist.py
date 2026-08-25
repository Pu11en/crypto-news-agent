from __future__ import annotations

import pytest

from xquik import XquikClient, XquikError


def test_tweet_fetch_rejects_accounts_outside_configured_allowlist(monkeypatch):
    client = XquikClient(api_key="test-key", allowed_usernames={"allowed_account"})
    called = False

    def fake_request(*args, **kwargs):
        nonlocal called
        called = True
        return {"tweets": []}

    monkeypatch.setattr(client, "_request", fake_request)

    with pytest.raises(XquikError, match="not in the configured accounts list"):
        client.fetch_recent_tweets("different_account", hours=24, limit=20)

    assert called is False
