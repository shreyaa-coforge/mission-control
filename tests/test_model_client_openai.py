"""Tests for OpenAIClient and the "openai" provider in get_model_client.

No network calls: the SDK client is a mock shaped like an OpenAI response,
and the autouse fixture in conftest.py stubs load_dotenv.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.model_client import (
    FakeModel,
    ModelError,
    ModelResponse,
    OpenAIClient,
    get_model_client,
)

FAST_MODEL = "fast-model-name"
STRONG_MODEL = "strong-model-name"
FAKE_KEY = "sk-test-key-not-real"

OPENAI_VARS = {
    "OPENAI_API_KEY": FAKE_KEY,
    "OPENAI_MODEL_FAST": FAST_MODEL,
    "OPENAI_MODEL_STRONG": STRONG_MODEL,
}


def make_response(content="hello", prompt_tokens=12, completion_tokens=7, usage=True):
    message = SimpleNamespace(role="assistant", content=content)
    choice = SimpleNamespace(index=0, message=message, finish_reason="stop")
    usage_obj = (
        SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )
        if usage
        else None
    )
    return SimpleNamespace(
        id="chatcmpl-test", object="chat.completion", choices=[choice], usage=usage_obj
    )


def make_sdk(response=None, side_effect=None):
    sdk = MagicMock()
    sdk.chat.completions.create = AsyncMock(
        return_value=response if response is not None else make_response(),
        side_effect=side_effect,
    )
    return sdk


def make_client(sdk):
    return OpenAIClient(
        api_key=FAKE_KEY,
        models={"fast": FAST_MODEL, "strong": STRONG_MODEL},
        sdk_client=sdk,
    )


def sent_kwargs(sdk):
    sdk.chat.completions.create.assert_awaited_once()
    return sdk.chat.completions.create.await_args.kwargs


# --- OpenAIClient.complete -------------------------------------------------


async def test_fast_role_sends_fast_model_name():
    sdk = make_sdk()
    await make_client(sdk).complete("classify", "fast", "sys", "user")

    kwargs = sent_kwargs(sdk)
    assert kwargs["model"] == FAST_MODEL
    assert "temperature" not in kwargs
    assert kwargs["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "user"},
    ]


async def test_strong_role_sends_strong_model_name():
    sdk = make_sdk()
    await make_client(sdk).complete("brief", "strong", "sys", "user")

    assert sent_kwargs(sdk)["model"] == STRONG_MODEL


async def test_json_mode_sets_response_format_and_mentions_json():
    sdk = make_sdk()
    await make_client(sdk).complete(
        "classify", "fast", "Classify the report.", "user", json_mode=True
    )

    kwargs = sent_kwargs(sdk)
    assert kwargs["response_format"] == {"type": "json_object"}
    system_msg = kwargs["messages"][0]
    assert system_msg["role"] == "system"
    assert "JSON" in system_msg["content"]
    assert system_msg["content"].startswith("Classify the report.")


async def test_json_mode_does_not_duplicate_existing_json_instruction():
    sdk = make_sdk()
    system = "Return JSON with keys a and b."
    await make_client(sdk).complete("classify", "fast", system, "user", json_mode=True)

    assert sent_kwargs(sdk)["messages"][0]["content"] == system


async def test_json_mode_false_sends_no_response_format():
    sdk = make_sdk()
    await make_client(sdk).complete("classify", "fast", "sys", "user", json_mode=False)

    kwargs = sent_kwargs(sdk)
    assert "response_format" not in kwargs
    assert kwargs["messages"][0]["content"] == "sys"


async def test_text_and_token_counts_read_from_response():
    sdk = make_sdk(make_response("all clear", prompt_tokens=33, completion_tokens=4))
    resp = await make_client(sdk).complete("classify", "strong", "sys", "user")

    assert resp == ModelResponse(
        text="all clear",
        input_tokens=33,
        output_tokens=4,
        model_role="strong",
        step="classify",
    )


async def test_missing_usage_gives_zero_tokens():
    sdk = make_sdk(make_response("ok", usage=False))
    resp = await make_client(sdk).complete("classify", "fast", "sys", "user")

    assert resp.text == "ok"
    assert resp.input_tokens == 0
    assert resp.output_tokens == 0


async def test_none_content_gives_empty_text():
    sdk = make_sdk(make_response(content=None))
    resp = await make_client(sdk).complete("classify", "fast", "sys", "user")

    assert resp.text == ""


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


async def test_sdk_exception_message_never_contains_key():
    sdk = make_sdk(side_effect=RuntimeError(f"bad key {FAKE_KEY} rejected"))
    with pytest.raises(ModelError) as exc_info:
        await make_client(sdk).complete("classify", "fast", "sys", "user")

    message = str(exc_info.value)
    assert FAKE_KEY not in message
    assert "rejected" in message


# --- get_model_client ------------------------------------------------------


def test_factory_returns_openai_client_when_vars_set(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "openai")
    for name, value in OPENAI_VARS.items():
        monkeypatch.setenv(name, value)

    client = get_model_client()

    assert isinstance(client, OpenAIClient)
    assert client.models == {"fast": FAST_MODEL, "strong": STRONG_MODEL}


def test_factory_openai_missing_vars_names_them(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)

    with pytest.raises(ModelError) as exc_info:
        get_model_client()

    message = str(exc_info.value)
    assert "OPENAI_MODEL_FAST" in message
    assert "OPENAI_MODEL_STRONG" in message
    # Set variables are not reported, and the key value never leaks.
    assert "OPENAI_API_KEY" not in message
    assert FAKE_KEY not in message


def test_factory_openai_all_vars_missing_names_all(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "openai")

    with pytest.raises(ModelError) as exc_info:
        get_model_client()

    message = str(exc_info.value)
    for name in OPENAI_VARS:
        assert name in message


def test_factory_defaults_to_openai_when_provider_unset(monkeypatch):
    monkeypatch.delenv("MODEL_PROVIDER", raising=False)
    for name in OPENAI_VARS:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ModelError) as exc_info:
        get_model_client()

    assert "OPENAI_API_KEY" in str(exc_info.value)


def test_factory_unknown_provider_lists_allowed_values(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "banana")

    with pytest.raises(ModelError) as exc_info:
        get_model_client()

    message = str(exc_info.value)
    assert "banana" in message
    assert "Allowed values: openai, fake" in message
    assert "azure" not in message


def test_factory_azure_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "azure")

    with pytest.raises(ModelError, match="Unknown MODEL_PROVIDER 'azure'"):
        get_model_client()


def test_factory_returns_fake_model_when_provider_fake(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "fake")

    assert isinstance(get_model_client(), FakeModel)
