"""B.AI model-provider plugin.

Subclasses the built-in DeepSeekProfile so that the V4 thinking-mode
wire handling (build_api_kwargs_extras) is inherited automatically.
Only the identity fields change: name, base_url, env_vars.

If DeepSeekProfile is not importable (e.g. the bundled provider lives
under a path Python can't import due to the dash in `model-providers`,
or this Hermes version doesn't ship deepseek), we fall back to bare
ProviderProfile with a copy of the thinking-mode logic.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("crypto-intel.bai-provider")

try:
    from providers import register_provider  # type: ignore[import]

    _REGISTER = register_provider
except ImportError:  # pragma: no cover - hermes provides this
    _REGISTER = lambda p: None  # noqa: E731

try:
    # Preferred: inherit thinking-mode handling from the bundled deepseek provider.
    # NOTE: in the deployed Hermes the path is `plugins/model-providers/deepseek/`
    # (with a dash); Python's standard import system can't import modules with
    # dashes. Hermes' plugin loader handles the dash, but our eager import may
    # not run before the loader pre-imports. The fallback below is what we
    # actually rely on unless a module alias is set up.
    from plugins.model_providers.deepseek import DeepSeekProfile  # type: ignore[import]  # noqa: F401

    _Base = DeepSeekProfile
    _USING_DEEPSEEK_INHERIT = True
except ImportError:
    from providers.base import ProviderProfile  # type: ignore[import]

    class _DeepSeekFallback(ProviderProfile):  # type: ignore[misc]
        """Minimal thinking-mode handling copied from DeepSeekProfile.

        Source-of-truth reference: hermes-agent
        plugins/model-providers/deepseek/__init__.py
        """

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
    _USING_DEEPSEEK_INHERIT = False


class BaiProfile(_Base):  # type: ignore[valid-type]
    """B.AI provider — routes to https://api.b.ai/v1."""

    pass


# The aux/fallback model is intentionally a different model than the
# primary so that the fallback path actually has somewhere to go.
# We try deepseek-v4-flash first (cheaper, still V4 family).
_AUX_MODEL = os.environ.get("BAI_AUX_MODEL", "deepseek-v4-flash")

bai = BaiProfile(
    name="bai",
    aliases=("b.ai", "bai-deepseek"),
    env_vars=("BAI_API_KEY",),
    display_name="B.AI",
    base_url="https://api.b.ai/v1",
    fallback_models=("deepseek-v4-pro", _AUX_MODEL),
    default_aux_model=_AUX_MODEL,
)
_REGISTER(bai)
log.info("bai provider registered (base_url=%s, inherit=%s)", bai.base_url, _USING_DEEPSEEK_INHERIT)
