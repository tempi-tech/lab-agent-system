import pytest

from src.core.llm import get_llm_client, OpenRouterLLM, GeminiLLM, ClaudeLLM


def test_get_llm_client_openrouter():
    client = get_llm_client("openrouter", "deepseek/deepseek-chat")
    assert isinstance(client, OpenRouterLLM)


def test_get_llm_client_gemini():
    client = get_llm_client("gemini", "gemini-3-flash-preview")
    assert isinstance(client, GeminiLLM)


def test_get_llm_client_claude():
    client = get_llm_client("claude", "claude-sonnet-4-5")
    assert isinstance(client, ClaudeLLM)


def test_get_llm_client_invalid():
    with pytest.raises(ValueError):
        get_llm_client("unknown", "model")
