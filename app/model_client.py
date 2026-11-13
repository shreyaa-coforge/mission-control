"""Model client interface and a fake for tests.

Every model call in the app goes through ``ModelClient``. Tests use
``FakeModel``, which never imports ``openai`` and never touches the network.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


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
