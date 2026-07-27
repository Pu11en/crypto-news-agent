"""LLM client with automatic fallback: z.ai GLM -> DeepSeek (on rate-limit / error).

z.ai GLM is the primary provider. When it rate-limits or fails, calls fall
through to DeepSeek transparently. Both are OpenAI-compatible.
"""

from __future__ import annotations

import json
import logging
from typing import Sequence

from openai import OpenAI

from config import Settings

log = logging.getLogger("agent.llm")

_REASONING_MODEL_MARKERS = ("reasoner", "reasoning", "r1", "v4")


class LLMError(Exception):
    pass


class LLMClient:
    """Primary + fallback LLM client.

    Falls through transparently: callers get the same response shape
    regardless of which provider served the request.
    """

    def __init__(self, settings: Settings):
        self._zai = OpenAI(
            api_key=settings.zai_api_key,
            base_url=settings.zai_base_url,
        )
        self._zai_model = settings.zai_model
        # GLM ships with hybrid thinking on by default: disabled globally
        # because reasoning tokens eat the max_tokens budget and can return
        # empty content. DeepSeek does not use this key.
        # https://docs.z.ai/guides/capabilities/thinking-mode
        self._zai_extra = {"thinking": {"type": "disabled"}}

        self._ds = (
            OpenAI(
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
            )
            if settings.deepseek_api_key
            else None
        )
        self._ds_model = settings.deepseek_model

    # ----------------------------------------------------------- internal

    @staticmethod
    def _content_from_response(resp) -> tuple[str, str | None, int]:
        """Return visible content plus finish/reasoning diagnostics.

        Some fallback models spend completion budget on hidden reasoning and
        return an empty `message.content` with `finish_reason == "length"`.
        Telegram cannot send empty messages, and JSON callers cannot parse
        them, so empty content must be treated as provider failure.
        """
        choice = resp.choices[0]
        msg = choice.message
        content = (msg.content or "").strip()
        finish_reason = getattr(choice, "finish_reason", None)
        reasoning = getattr(msg, "reasoning_content", "") or ""
        return content, finish_reason, len(reasoning)

    def _deepseek_max_tokens(self, requested: int) -> int:
        """Leave room for reasoning tokens on DeepSeek reasoning-style models."""
        model = (self._ds_model or "").lower()
        if any(marker in model for marker in _REASONING_MODEL_MARKERS):
            return max(requested, 4096)
        return requested

    def _call(
        self,
        *,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
    ) -> str:
        """Try z.ai GLM, fall through to DeepSeek on any error."""
        errors: list[str] = []

        # Primary: z.ai GLM
        try:
            resp = self._zai.chat.completions.create(
                model=self._zai_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                extra_body=self._zai_extra,
            )
            content, finish_reason, reasoning_chars = self._content_from_response(resp)
            if content:
                return content
            raise LLMError(
                "empty content "
                f"(finish_reason={finish_reason}, reasoning_chars={reasoning_chars})"
            )
        except Exception as e:
            msg = f"z.ai {self._zai_model}: {e}"
            errors.append(msg)
            log.warning("primary LLM failed: %s", msg)

        # Fallback: DeepSeek
        if self._ds is not None:
            try:
                ds_max_tokens = self._deepseek_max_tokens(max_tokens)
                resp = self._ds.chat.completions.create(
                    model=self._ds_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=ds_max_tokens,
                )
                content, finish_reason, reasoning_chars = self._content_from_response(resp)
                if not content and finish_reason == "length":
                    retry_tokens = max(ds_max_tokens * 2, 8192)
                    log.warning(
                        "fallback DeepSeek returned empty content after %d tokens "
                        "(reasoning_chars=%d); retrying with %d tokens",
                        ds_max_tokens,
                        reasoning_chars,
                        retry_tokens,
                    )
                    resp = self._ds.chat.completions.create(
                        model=self._ds_model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=retry_tokens,
                    )
                    content, finish_reason, reasoning_chars = self._content_from_response(resp)
                if content:
                    log.info("fallback DeepSeek ok (%d chars)", len(content))
                    return content
                msg = (
                    f"DeepSeek {self._ds_model}: empty content "
                    f"(finish_reason={finish_reason}, reasoning_chars={reasoning_chars})"
                )
                errors.append(msg)
                log.error("fallback LLM returned empty content: %s", msg)
            except Exception as e:
                msg = f"DeepSeek {self._ds_model}: {e}"
                errors.append(msg)
                log.exception("fallback LLM also failed")
        else:
            errors.append("DeepSeek not configured")

        raise LLMError(" | ".join(errors))

    # --------------------------------------------------------- public API

    def _chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.7,
        max_tokens: int = 1200,
    ) -> str:
        return self._call(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def curate(self, system: str, user_prompt: str) -> dict:
        """Structured curation: expects JSON. Lower temp, defensive parse."""
        raw = self._chat(
            system, user_prompt, temperature=0.3, max_tokens=3000
        )
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
            if cleaned.endswith("```"):
                cleaned = cleaned.rsplit("```", 1)[0]
            cleaned = cleaned.strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            log.error("curation non-JSON:\n%s", raw[:500])
            raise LLMError("Curation did not return valid JSON")

    def structured(
        self,
        system: str,
        user_prompt: str,
        *,
        temperature: float = 0.25,
        max_tokens: int = 4000,
    ) -> dict:
        """Request and defensively parse a JSON object from either provider."""
        raw = self._chat(
            system,
            user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            first_newline = cleaned.find("\n")
            cleaned = cleaned[first_newline + 1 :] if first_newline >= 0 else ""
            if cleaned.rstrip().endswith("```"):
                cleaned = cleaned.rstrip()[:-3]
            cleaned = cleaned.strip()
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            log.error("structured response was not JSON: %s", raw[:500])
            raise LLMError("Structured response did not return valid JSON") from exc
        if not isinstance(parsed, dict):
            raise LLMError("Structured response must be a JSON object")
        return parsed

    def write_script(self, system: str, user_prompt: str) -> str:
        return self._chat(system, user_prompt, temperature=0.75, max_tokens=400)

    def refine_script(self, system: str, user_prompt: str) -> str:
        return self._chat(system, user_prompt, temperature=0.7, max_tokens=400)

    def chat(
        self,
        system: str,
        history: Sequence[dict],
        user_message: str,
    ) -> str:
        """Free-form chat with rolling history."""
        messages = [{"role": "system", "content": system}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_message})
        return self._call(
            messages=list(messages),
            temperature=0.7,
            max_tokens=800,
        )
