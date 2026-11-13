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


class AzureOpenAIClient(ModelClient):
    """Real model client backed by Azure OpenAI.

    ``deployments`` maps a role ("fast", "strong") to an Azure deployment
    name. ``sdk_client`` may be injected so tests never touch the network.
    """

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        api_version: str,
        deployments: dict[str, str],
        sdk_client: Any = None,
    ) -> None:
        self.deployments = dict(deployments)
        if sdk_client is None:
            # Imported here so FakeModel users never need ``openai`` loaded.
            import openai

            sdk_client = openai.AsyncAzureOpenAI(
                azure_endpoint=endpoint,
                api_key=api_key,
                api_version=api_version,
            )
        self._client = sdk_client

    async def complete(
        self,
        step: str,
        role: str,
        system: str,
        user: str,
        json_mode: bool = False,
    ) -> ModelResponse:
        deployment = self.deployments.get(role)
        if deployment is None:
            raise ModelError(
                f"Unknown model role '{role}'. "
                f"Known roles: {sorted(self.deployments)}"
            )

        kwargs: dict[str, Any] = {
            "model": deployment,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise ModelError(f"Azure OpenAI call failed: {exc}") from exc

        try:
            text = response.choices[0].message.content or ""
        except (AttributeError, IndexError, TypeError) as exc:
            raise ModelError(f"Unexpected Azure OpenAI response shape: {exc}") from exc

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


ALLOWED_PROVIDERS = ("azure", "fake")

_AZURE_ENV_VARS = (
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_API_VERSION",
    "MODEL_FAST_DEPLOYMENT",
    "MODEL_STRONG_DEPLOYMENT",
)


def get_model_client() -> ModelClient:
    """Build the model client chosen by the MODEL_PROVIDER env variable."""
    load_dotenv()
    provider = (os.getenv("MODEL_PROVIDER") or "azure").strip().lower()

    if provider == "fake":
        # Empty script for now; we will give it demo replies in the
        # pipeline branch.
        return FakeModel(script={})

    if provider == "azure":
        values = {name: (os.getenv(name) or "").strip() for name in _AZURE_ENV_VARS}
        missing = [name for name, value in values.items() if not value]
        if missing:
            # Only variable names are reported, never values.
            raise ModelError(
                "Missing Azure OpenAI environment variables: " + ", ".join(missing)
            )
        return AzureOpenAIClient(
            endpoint=values["AZURE_OPENAI_ENDPOINT"],
            api_key=values["AZURE_OPENAI_API_KEY"],
            api_version=values["AZURE_OPENAI_API_VERSION"],
            deployments={
                "fast": values["MODEL_FAST_DEPLOYMENT"],
                "strong": values["MODEL_STRONG_DEPLOYMENT"],
            },
        )

    raise ModelError(
        f"Unknown MODEL_PROVIDER '{provider}'. "
        f"Allowed values: {', '.join(ALLOWED_PROVIDERS)}"
    )
