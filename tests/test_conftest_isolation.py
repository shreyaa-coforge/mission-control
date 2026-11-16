"""Checks that the autouse fixture in conftest.py isolates every test."""

import os

from app.model_client import FakeModel, get_model_client

SECRET_VARS = (
    "OPENAI_API_KEY",
)


def test_secret_env_vars_are_absent():
    for name in SECRET_VARS:
        assert name not in os.environ


def test_provider_is_fake():
    assert os.environ["MODEL_PROVIDER"] == "fake"


def test_factory_returns_fake_and_dotenv_does_not_refill_secrets():
    assert isinstance(get_model_client(), FakeModel)
    for name in SECRET_VARS:
        assert name not in os.environ
