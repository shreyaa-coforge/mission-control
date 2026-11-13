"""Tests for AzureOpenAIClient and get_model_client.

No network calls: the SDK client is a mock, and load_dotenv is stubbed so a
local .env file can never leak into these tests.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.model_client as mc
from app.model_client import (
    AzureOpenAIClient,
    FakeModel,
    ModelError,
    ModelResponse,
    get_model_client,
)

DEPLOYMENTS = {"fast": "fast-deploy", "strong": "strong-deploy"}

AZURE_VARS = {
    "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com",
    "AZURE_OPENAI_API_KEY": "test-key-not-real",
    "AZURE_OPENAI_API_VERSION": "2024-06-01",
    "MODEL_FAST_DEPLOYMENT": "fast-deploy",
    "MODEL_STRONG_DEPLOYMENT": "strong-deploy",
}


def make_response(content="hello", prompt_tokens=12, completion_tokens=7, usage=True):
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    usage_obj = (
        SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
        if usage
        else None
    )
    return SimpleNamespace(choices=[choice], usage=usage_obj)


def make_sdk(response=None, side_effect=None):
    sdk = MagicMock()
    sdk.chat.completions.create = AsyncMock(
        return_value=response if response is not None else make_response(),
        side_effect=side_effect,
    )
    return sdk


def make_client(sdk):
    return AzureOpenAIClient(
        endpoint="https://example.openai.azure.com",
        api_key="test-key-not-real",
        api_version="2024-06-01",
        deployments=DEPLOYMENTS,
        sdk_client=sdk,
    )


# --- AzureOpenAIClient.complete -------------------------------------------


async def test_fast_role_uses_fast_deployment():
    sdk = make_sdk()
    await make_client(sdk).complete("classify", "fast", "sys", "user")

    kwargs = sdk.chat.completions.create.await_args.kwargs
    assert kwargs["model"] == "fast-deploy"
    assert kwargs["temperature"] == 0
    assert kwargs["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "user"},
    ]


async def test_strong_role_uses_strong_deployment():
    sdk = make_sdk()
    await make_client(sdk).complete("draft", "strong", "sys", "user")

    assert sdk.chat.completions.create.await_args.kwargs["model"] == "strong-deploy"


async def test_json_mode_true_passes_response_format():
    sdk = make_sdk()
    await make_client(sdk).complete("draft", "fast", "sys", "user", json_mode=True)

    kwargs = sdk.chat.completions.create.await_args.kwargs
    assert kwargs["response_format"] == {"type": "json_object"}


async def test_json_mode_false_omits_response_format():
    sdk = make_sdk()
    await make_client(sdk).complete("draft", "fast", "sys", "user", json_mode=False)

    assert "response_format" not in sdk.chat.completions.create.await_args.kwargs


async def test_text_and_tokens_read_into_model_response():
    sdk = make_sdk(make_response("the reply", prompt_tokens=42, completion_tokens=9))
    resp = await make_client(sdk).complete("classify", "fast", "sys", "user")

    assert isinstance(resp, ModelResponse)
    assert resp.text == "the reply"
    assert resp.input_tokens == 42
    assert resp.output_tokens == 9
    assert resp.model_role == "fast"
    assert resp.step == "classify"


async def test_none_content_becomes_empty_string():
    sdk = make_sdk(make_response(content=None))
    resp = await make_client(sdk).complete("classify", "fast", "sys", "user")

    assert resp.text == ""


async def test_missing_usage_gives_zero_tokens():
    sdk = make_sdk(make_response("ok", usage=False))
    resp = await make_client(sdk).complete("classify", "fast", "sys", "user")

    assert resp.text == "ok"
    assert resp.input_tokens == 0
    assert resp.output_tokens == 0


async def test_unknown_role_raises_model_error_without_calling_sdk():
    sdk = make_sdk()
    with pytest.raises(ModelError, match="medium"):
        await make_client(sdk).complete("classify", "medium", "sys", "user")

    sdk.chat.completions.create.assert_not_awaited()


async def test_sdk_exception_wrapped_in_model_error():
    sdk = make_sdk(side_effect=RuntimeError("rate limited by service"))
    with pytest.raises(ModelError, match="rate limited by service") as exc_info:
        await make_client(sdk).complete("classify", "fast", "sys", "user")

    assert isinstance(exc_info.value.__cause__, RuntimeError)


# --- get_model_client ------------------------------------------------------


@pytest.fixture
def clean_env(monkeypatch):
    """Stub load_dotenv and clear all model-related env variables."""
    monkeypatch.setattr(mc, "load_dotenv", lambda *a, **k: False)
    monkeypatch.delenv("MODEL_PROVIDER", raising=False)
    for name in AZURE_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_factory_returns_fake_model_when_provider_fake(clean_env):
    clean_env.setenv("MODEL_PROVIDER", "fake")

    assert isinstance(get_model_client(), FakeModel)


def test_factory_azure_missing_vars_names_them(clean_env):
    clean_env.setenv("MODEL_PROVIDER", "azure")
    clean_env.setenv("AZURE_OPENAI_ENDPOINT", AZURE_VARS["AZURE_OPENAI_ENDPOINT"])
    clean_env.setenv("AZURE_OPENAI_API_KEY", AZURE_VARS["AZURE_OPENAI_API_KEY"])
    clean_env.setenv("MODEL_FAST_DEPLOYMENT", AZURE_VARS["MODEL_FAST_DEPLOYMENT"])

    with pytest.raises(ModelError) as exc_info:
        get_model_client()

    message = str(exc_info.value)
    assert "AZURE_OPENAI_API_VERSION" in message
    assert "MODEL_STRONG_DEPLOYMENT" in message
    # Variables that are set are not reported, and the key value never leaks.
    assert "AZURE_OPENAI_ENDPOINT" not in message
    assert "AZURE_OPENAI_API_KEY" not in message
    assert AZURE_VARS["AZURE_OPENAI_API_KEY"] not in message


def test_factory_defaults_to_azure_when_provider_unset(clean_env):
    with pytest.raises(ModelError, match="AZURE_OPENAI_ENDPOINT"):
        get_model_client()


def test_factory_unknown_provider_raises(clean_env):
    clean_env.setenv("MODEL_PROVIDER", "banana")

    with pytest.raises(ModelError) as exc_info:
        get_model_client()

    message = str(exc_info.value)
    assert "banana" in message
    assert "azure" in message
    assert "fake" in message
