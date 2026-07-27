from __future__ import annotations

import pytest

from llm import LLMClient, LLMError


class FakeStructuredLLM(LLMClient):
    def __init__(self, response: str):
        self.response = response

    def _chat(self, *args, **kwargs):
        return self.response


def test_structured_accepts_json_code_fence():
    llm = FakeStructuredLLM('```json\n{"scenes": [{"id": "scene-01"}]}\n```')

    result = llm.structured("system", "user")

    assert result["scenes"][0]["id"] == "scene-01"


def test_structured_rejects_non_json():
    llm = FakeStructuredLLM("I think a timeline would look good")

    with pytest.raises(LLMError, match="valid JSON"):
        llm.structured("system", "user")
