"""Model client interface and a fake for tests.

Every model call in the app goes through ``ModelClient``. Tests use
``FakeModel``, which never imports ``openai`` and never touches the network.
"""

from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv


@dataclass
class ModelResponse:
    text: str
    input_tokens: int
    output_tokens: int
    model_role: str  # "fast" or "strong"
    step: str  # which pipeline step made the call


class ModelError(Exception):
    """Raised for any model failure."""


class ModelClient(ABC):
    @abstractmethod
    async def complete(
        self,
        step: str,
        role: str,
        system: str,
        user: str,
        json_mode: bool = False,
    ) -> ModelResponse:
        """Send one prompt to the model and return its reply."""


class FakeModel(ModelClient):
    """Scripted model for tests.

    ``script`` maps a step name to:
      - a string: always return that text
      - a list of strings: return them in order, then raise ModelError
      - an Exception instance: raise it
    """

    def __init__(
        self,
        script: dict[str, Any],
        input_tokens: int = 100,
        output_tokens: int = 50,
        delay: float = 0.0,
    ) -> None:
        self.script = script
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.delay = delay
        self.calls: list[dict[str, Any]] = []
        self._list_positions: dict[str, int] = {}

    async def complete(
        self,
        step: str,
        role: str,
        system: str,
        user: str,
        json_mode: bool = False,
    ) -> ModelResponse:
        self.calls.append(
            {
                "step": step,
                "role": role,
                "system": system,
                "user": user,
                "json_mode": json_mode,
            }
        )

        if self.delay > 0:
            await asyncio.sleep(self.delay)

        if step not in self.script:
            raise ModelError(f"FakeModel has no script for step '{step}'")

        entry = self.script[step]

        if isinstance(entry, BaseException):
            raise entry

        if isinstance(entry, str):
            text = entry
        elif isinstance(entry, list):
            pos = self._list_positions.get(step, 0)
            if pos >= len(entry):
                raise ModelError(
                    f"FakeModel script for step '{step}' ran out of replies "
                    f"after {len(entry)} call(s)"
                )
            self._list_positions[step] = pos + 1
            text = entry[pos]
        else:
            raise ModelError(
                f"FakeModel script for step '{step}' has unsupported type "
                f"{type(entry).__name__}"
            )

        return ModelResponse(
            text=text,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            model_role=role,
            step=step,
        )


class OpenAIClient(ModelClient):
    """Real model client backed by the public OpenAI API.

    ``models`` maps a role ("fast", "strong") to an OpenAI model name.
    ``sdk_client`` may be injected so tests never touch the network.
    """

    def __init__(
        self,
        api_key: str,
        models: dict[str, str],
        sdk_client: Any = None,
    ) -> None:
        self.models = dict(models)
        # Kept only so it can be scrubbed from error messages.
        self._api_key = api_key
        if sdk_client is None:
            # Imported here so FakeModel users never need ``openai`` loaded.
            import openai

            sdk_client = openai.AsyncOpenAI(api_key=api_key)
        self._client = sdk_client

    async def complete(
        self,
        step: str,
        role: str,
        system: str,
        user: str,
        json_mode: bool = False,
    ) -> ModelResponse:
        model = self.models.get(role)
        if model is None:
            raise ModelError(
                f"Unknown model role '{role}'. Known roles: {sorted(self.models)}"
            )

        if json_mode and "JSON" not in system:
            # OpenAI rejects json_object mode unless "JSON" is in the messages.
            suffix = "Respond with a single JSON object."
            system = f"{system.rstrip()}\n\n{suffix}" if system.strip() else suffix

        return await _chat_complete(
            self._client,
            label="OpenAI",
            model=model,
            step=step,
            role=role,
            system=system,
            user=user,
            json_mode=json_mode,
            secret=self._api_key,
        )


def _redact(message: str, secret: str | None) -> str:
    if secret:
        message = message.replace(secret, "***")
    return message


async def _chat_complete(
    sdk_client: Any,
    *,
    label: str,
    model: str,
    step: str,
    role: str,
    system: str,
    user: str,
    json_mode: bool,
    secret: str | None = None,
) -> ModelResponse:
    """Call chat.completions.create and build a ModelResponse.

    Used by OpenAIClient. ``secret`` (if given) is scrubbed from any error
    message.
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    try:
        response = await sdk_client.chat.completions.create(**kwargs)
    except Exception as exc:
        raise ModelError(_redact(f"{label} call failed: {exc}", secret)) from exc

    try:
        text = response.choices[0].message.content or ""
    except (AttributeError, IndexError, TypeError) as exc:
        raise ModelError(
            _redact(f"Unexpected {label} response shape: {exc}", secret)
        ) from exc

    usage = getattr(response, "usage", None)
    input_tokens = (getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
    output_tokens = (getattr(usage, "completion_tokens", 0) or 0) if usage else 0

    return ModelResponse(
        text=text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model_role=role,
        step=step,
    )


ALLOWED_PROVIDERS = ("openai", "fake")

_OPENAI_ENV_VARS = (
    "OPENAI_API_KEY",
    "OPENAI_MODEL_FAST",
    "OPENAI_MODEL_STRONG",
)

def get_model_client() -> ModelClient:
    """Build the model client chosen by the MODEL_PROVIDER env variable."""
    # override=False: variables already in the environment (e.g. set by the
    # test fixture or the shell) always win over values in .env.
    load_dotenv(override=False)
    provider = (os.getenv("MODEL_PROVIDER") or "openai").strip().lower()

    if provider == "fake":
        # Empty script for now; we will give it demo replies in the
        # pipeline branch.
        return FakeModel(script={})

    if provider == "openai":
        values = {name: (os.getenv(name) or "").strip() for name in _OPENAI_ENV_VARS}
        missing = [name for name, value in values.items() if not value]
        if missing:
            # Only variable names are reported, never values.
            raise ModelError(
                "Missing OpenAI environment variables: " + ", ".join(missing)
            )
        return OpenAIClient(
            api_key=values["OPENAI_API_KEY"],
            models={
                "fast": values["OPENAI_MODEL_FAST"],
                "strong": values["OPENAI_MODEL_STRONG"],
            },
        )

    raise ModelError(
        f"Unknown MODEL_PROVIDER '{provider}'. "
        f"Allowed values: {', '.join(ALLOWED_PROVIDERS)}"
    )
