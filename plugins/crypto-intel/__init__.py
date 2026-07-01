"""Crypto Twitter intelligence plugin for Hermes Agent.

Register() loops over (name, schema, handler, emoji) tuples and calls
ctx.register_tool() — mirrors the bundled spotify plugin.

check_fn gates every tool on BAI_API_KEY + XQUIK_API_KEY being set.
"""

from __future__ import annotations

from .tools import (
    GET_ALERTS_SCHEMA,
    GET_DIGEST_SCHEMA,
    GET_IDEAS_SCHEMA,
    GET_STATS_SCHEMA,
    RUN_PIPELINE_SCHEMA,
    SEARCH_TWEETS_SCHEMA,
    _check_keys,
    _handle_get_alerts,
    _handle_get_digest,
    _handle_get_ideas,
    _handle_get_stats,
    _handle_run_pipeline,
    _handle_search_tweets,
)

_TOOLS = (
    ("crypto_run_pipeline", RUN_PIPELINE_SCHEMA, _handle_run_pipeline, "🚀"),
    ("crypto_get_digest", GET_DIGEST_SCHEMA, _handle_get_digest, "📰"),
    ("crypto_get_alerts", GET_ALERTS_SCHEMA, _handle_get_alerts, "🚨"),
    ("crypto_get_ideas", GET_IDEAS_SCHEMA, _handle_get_ideas, "💡"),
    ("crypto_search_tweets", SEARCH_TWEETS_SCHEMA, _handle_search_tweets, "🔎"),
    ("crypto_get_stats", GET_STATS_SCHEMA, _handle_get_stats, "📊"),
)


def register(ctx) -> None:
    """Register all crypto-intel tools with the agent runtime."""
    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset="crypto-intel",
            schema=schema,
            handler=handler,
            check_fn=_check_keys,
            emoji=emoji,
        )
