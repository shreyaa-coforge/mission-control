"""Shared pytest fixtures.

The autouse fixture below guarantees every test runs with no network access
and no API key: all real model credentials are removed from the environment,
MODEL_PROVIDER is forced to "fake", and load_dotenv is stubbed so a local
.env file can never put the real credentials back.
"""

import pytest

import app.model_client as mc

_MODEL_ENV_VARS = (
    "OPENAI_API_KEY",
    "OPENAI_MODEL_FAST",
    "OPENAI_MODEL_STRONG",
)


@pytest.fixture(autouse=True)
def _no_real_model(monkeypatch):
    for name in _MODEL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    # get_model_client() calls load_dotenv(override=False). That keeps
    # MODEL_PROVIDER=fake, but would refill the deleted variables above from
    # .env, so stub it out entirely for tests.
    monkeypatch.setattr(mc, "load_dotenv", lambda *a, **k: False)
    yield
