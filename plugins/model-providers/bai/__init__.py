"""B.AI model-provider plugin.

Subclasses the built-in DeepSeekProfile so that the V4 thinking-mode
wire handling (build_api_kwargs_extras) is inherited automatically.
Only the identity fields change: name, base_url, env_vars.

If DeepSeekProfile is not importable (e.g. different Hermes version),
we fall back to ProviderProfile with a copy of the thinking-mode logic.
"""

from __future__ import annotations

from providers import register_provider

try:
    # Preferred: inherit thinking-mode handling from the bundled deepseek provider.
    from plugins.model_providers.deepseek import DeepSeekProfile  # type: ignore[import]

    _Base = DeepSeekProfile
except ImportError:
    # Fallback: use bare ProviderProfile and re-implement the thinking toggle.
    from providers.base import ProviderProfile  # type: ignore[import]

    class _DeepSeekFallback(ProviderProfile):  # type: ignore[misc]
        """Minimal thinking-mode handling copied from DeepSeekProfile."""

        def build_api_kwargs_extras(
            self, *, reasoning_config=None, model=None, **context
        ):
            extra_body: dict = {}
            top_level: dict = {}
            # Disable thinking by default for tool-routing (avoids reasoning_content 400).
            enabled = True
            if isinstance(reasoning_config, dict):
                if reasoning_config.get("enabled") is False:
                    enabled = False
            extra_body["thinking"] = {
                "type": "enabled" if enabled else "disabled"
            }
            if not enabled:
                return extra_body, top_level
            if isinstance(reasoning_config, dict):
                effort = (reasoning_config.get("effort") or "").strip().lower()
                if effort in {"xhigh", "max"}:
                    top_level["reasoning_effort"] = "max"
                elif effort in {"low", "medium", "high"}:
                    top_level["reasoning_effort"] = effort
            return extra_body, top_level

    _Base = _DeepSeekFallback


class BaiProfile(_Base):  # type: ignore[valid-type]
    """B.AI provider — routes to https://api.b.ai/v1."""

    pass


bai = BaiProfile(
    name="bai",
    aliases=("b.ai", "bai-deepseek"),
    env_vars=("BAI_API_KEY",),
    display_name="B.AI",
    base_url="https://api.b.ai/v1",
    fallback_models=("deepseek-v4-pro",),
    default_aux_model="deepseek-v4-pro",
)
register_provider(bai)
