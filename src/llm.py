"""Thin wrapper around the z.ai GLM API via the OpenAI SDK.

z.ai is OpenAI-compatible: same SDK, just point base_url at the Coding Plan
endpoint and use their model names. We expose three call shapes used by the
bot: structured curation (JSON), script generation, and free-form chat with
history.
"""

from __future__ import annotations

import json
import logging
from typing import Sequence

from openai import OpenAI

from config import Settings

log = logging.getLogger("agent.llm")


class LLMError(Exception):
    pass


class GLMClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = OpenAI(
            api_key=settings.zai_api_key,
            base_url=settings.zai_base_url,
        )
        self.model = settings.zai_model
        # GLM-4.6 ships with hybrid thinking on by default — reasoning tokens
        # eat the max_tokens budget and can return empty content. Disabled
        # globally: this bot's prompts are explicit enough not to need it.
        # Ref: https://docs.z.ai/guides/capabilities/thinking-mode
        self._extra_body = {"thinking": {"type": "disabled"}}

    def _chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.7,
        max_tokens: int = 1200,
    ) -> str:
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                extra_body=self._extra_body,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            log.exception("GLM call failed")
            raise LLMError(f"LLM call failed: {e}") from e

    def curate(
        self, system: str, user_prompt: str
    ) -> dict:
        """Call for curation — expects strict JSON back. Parses defensively."""
        raw = self._chat(
            system, user_prompt, temperature=0.3, max_tokens=1000
        )
        # Strip accidental markdown fences if the model adds them.
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
            if cleaned.endswith("```"):
                cleaned = cleaned.rsplit("```", 1)[0]
            cleaned = cleaned.strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            log.error("curation returned non-JSON:\n%s", raw[:500])
            raise LLMError("Curation did not return valid JSON")

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
        """Free-form chat with rolling history.

        history is a list of {"role": "user"|"assistant", "content": "..."} dicts.
        """
        messages = [{"role": "system", "content": system}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_message})
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.7,
                max_tokens=800,
                extra_body=self._extra_body,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            log.exception("GLM chat failed")
            raise LLMError(f"LLM chat failed: {e}") from e
