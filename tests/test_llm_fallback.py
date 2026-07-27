from __future__ import annotations

import pytest

from llm import LLMClient, LLMError


class FakeMessage:
    def __init__(self, content: str | None, reasoning_content: str = ""):
        self.content = content
        self.reasoning_content = reasoning_content


class FakeChoice:
    def __init__(
        self,
        content: str | None,
        *,
        finish_reason: str = "stop",
        reasoning_content: str = "",
    ):
        self.message = FakeMessage(content, reasoning_content)
        self.finish_reason = finish_reason


class FakeResponse:
    def __init__(
        self,
        content: str | None,
        *,
        finish_reason: str = "stop",
        reasoning_content: str = "",
    ):
        self.choices = [
            FakeChoice(
                content,
                finish_reason=finish_reason,
                reasoning_content=reasoning_content,
            )
        ]


class FakeCompletions:
    def __init__(self, responses=None, error: Exception | None = None):
        self.responses = list(responses or [])
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        if not self.responses:
            raise AssertionError("unexpected extra LLM call")
        return self.responses.pop(0)


class FakeChat:
    def __init__(self, completions: FakeCompletions):
        self.completions = completions


class FakeClient:
    def __init__(self, completions: FakeCompletions):
        self.chat = FakeChat(completions)


def build_client(*, ds_responses, ds_model: str = "deepseek-v4-pro"):
    llm = object.__new__(LLMClient)
    llm._zai = FakeClient(FakeCompletions(error=RuntimeError("rate limited")))
    llm._zai_model = "glm-5-turbo"
    llm._zai_extra = {"thinking": {"type": "disabled"}}
    llm._ds_completions = FakeCompletions(ds_responses)
    llm._ds = FakeClient(llm._ds_completions)
    llm._ds_model = ds_model
    return llm


def test_reasoning_fallback_gets_extra_completion_budget():
    llm = build_client(ds_responses=[FakeResponse("ok")])

    result = llm._call(
        messages=[{"role": "user", "content": "say ok"}],
        temperature=0,
        max_tokens=20,
    )

    assert result == "ok"
    assert llm._ds_completions.calls[0]["max_tokens"] == 4096


def test_empty_reasoning_response_retries_before_raising():
    llm = build_client(
        ds_responses=[
            FakeResponse("", finish_reason="length", reasoning_content="thinking"),
            FakeResponse("ok", finish_reason="stop"),
        ]
    )

    result = llm._call(
        messages=[{"role": "user", "content": "say ok"}],
        temperature=0,
        max_tokens=20,
    )

    assert result == "ok"
    assert [call["max_tokens"] for call in llm._ds_completions.calls] == [4096, 8192]


def test_empty_provider_responses_raise_llmerror():
    llm = build_client(ds_responses=[FakeResponse("", finish_reason="stop")])

    with pytest.raises(LLMError, match="empty content"):
        llm._call(
            messages=[{"role": "user", "content": "say ok"}],
            temperature=0,
            max_tokens=20,
        )
