from __future__ import annotations

import pytest

from auto_writing.llm import (
    FakeLLMClient,
    LLMProviderNotConfiguredError,
    LLMProviderTransportNotConfiguredError,
    LLMRequest,
    LLMResponse,
    RealProviderLLMClient,
    build_llm_client,
)


def test_fake_llm_client_returns_deterministic_structured_output() -> None:
    request = LLMRequest(
        stage="chapter-draft",
        prompt="Write chapter 1",
        response_format="json",
        metadata={"project_id": "p-1", "chapter": "1"},
    )
    client = FakeLLMClient()

    first = client.generate(request)
    second = client.generate(request)

    assert first == second
    assert first.provider == "fake"
    assert first.structured_output["stage"] == "chapter-draft"
    assert first.structured_output["response_format"] == "json"


def test_build_llm_client_defaults_to_fake_provider() -> None:
    client = build_llm_client(env={})

    request = LLMRequest(stage="summary", prompt="Summarize", metadata={})
    result = client.generate(request)

    assert result.provider == "fake"


def test_real_provider_missing_key_fails_before_transport_call() -> None:
    called = False

    def transport(_request: LLMRequest, _model: str, _api_key: str) -> LLMResponse:
        nonlocal called
        called = True
        return LLMResponse(provider="openai", model=_model, output_text="ok", structured_output={})

    client = RealProviderLLMClient(
        provider="openai",
        model="gpt-4o-mini",
        api_key=None,
        transport=transport,
    )

    with pytest.raises(LLMProviderNotConfiguredError):
        client.generate(LLMRequest(stage="draft", prompt="x"))

    assert called is False


def test_real_provider_without_transport_returns_controlled_error() -> None:
    client = RealProviderLLMClient(
        provider="openai",
        model="gpt-4o-mini",
        api_key="test-key",
        transport=None,
    )

    with pytest.raises(LLMProviderTransportNotConfiguredError):
        client.generate(LLMRequest(stage="draft", prompt="x"))
