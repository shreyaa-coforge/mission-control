"""Manual smoke test against the real model provider. NOT a pytest test.

Run with:  python scripts/smoke_model.py

Uses whatever MODEL_PROVIDER is set in .env (or the shell): openai or
fake. Sends one tiny request to each role ("fast" and "strong") and
prints the reply and token counts. API keys are never printed.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Make ``app`` importable when run as a plain script from the repo root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from app.model_client import FakeModel, ModelError, get_model_client  # noqa: E402

SYSTEM = "Reply with one word."
USER = "Say ready."
JSON_SYSTEM = "Reply in JSON."
JSON_USER = 'Return {"status": "ready"}'

_SECRET_VARS = ("OPENAI_API_KEY",)


def _redact(message: str) -> str:
    for name in _SECRET_VARS:
        key = os.getenv(name) or ""
        if key:
            message = message.replace(key, "***")
    return message


async def main() -> int:
    load_dotenv(ROOT / ".env", override=False)
    provider = (os.getenv("MODEL_PROVIDER") or "openai").strip().lower()
    print(f"Provider: {provider}")

    try:
        client = get_model_client()
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED to build model client: {_redact(str(exc))}")
        return 1

    if isinstance(client, FakeModel):
        # The factory's FakeModel has an empty script; give it replies so the
        # smoke run exercises the full path offline.
        client.script = {
            "smoke_fast": "ready",
            "smoke_strong": "ready",
            "smoke_fast_json": '{"status": "ready"}',
        }

    failures = 0
    for role in ("fast", "strong"):
        step = f"smoke_{role}"
        try:
            resp = await client.complete(step, role, SYSTEM, USER)
        except ModelError as exc:
            failures += 1
            print(f"FAILED step={step} role={role}: {_redact(str(exc))}")
            continue
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(
                f"FAILED step={step} role={role}: "
                f"{type(exc).__name__}: {_redact(str(exc))}"
            )
            continue
        print(
            f"OK role={resp.model_role} "
            f"reply={resp.text.strip()!r} "
            f"input_tokens={resp.input_tokens} output_tokens={resp.output_tokens}"
        )

    # One JSON-mode call on the fast role to confirm response_format works.
    step = "smoke_fast_json"
    try:
        resp = await client.complete(
            step, "fast", JSON_SYSTEM, JSON_USER, json_mode=True
        )
    except Exception as exc:  # noqa: BLE001
        failures += 1
        print(
            f"FAILED step={step} role=fast json_mode=True: "
            f"{type(exc).__name__}: {_redact(str(exc))}"
        )
    else:
        print(
            f"OK role={resp.model_role} json_mode=True "
            f"reply={resp.text.strip()!r} "
            f"input_tokens={resp.input_tokens} output_tokens={resp.output_tokens}"
        )

    if failures:
        print(f"Smoke test finished with {failures} failure(s).")
        return 1
    print("Smoke test passed for both roles and JSON mode.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
