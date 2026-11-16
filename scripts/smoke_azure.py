"""Manual smoke test against real Azure OpenAI. NOT a pytest test.

Run with:  python scripts/smoke_azure.py

Sends one tiny request to each role ("fast" and "strong") and prints the
reply and token counts. The API key is never printed.
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

from app.model_client import ModelError, get_model_client  # noqa: E402

SYSTEM = "Reply with one word."
USER = "Say ready."


def _redact(message: str) -> str:
    key = os.getenv("AZURE_OPENAI_API_KEY") or ""
    if key:
        message = message.replace(key, "***")
    return message


async def main() -> int:
    load_dotenv(ROOT / ".env", override=False)
    os.environ["MODEL_PROVIDER"] = "azure"

    try:
        client = get_model_client()
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED to build Azure client: {_redact(str(exc))}")
        return 1

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
            f"OK step={resp.step} role={resp.model_role} "
            f"reply={resp.text.strip()!r} "
            f"input_tokens={resp.input_tokens} output_tokens={resp.output_tokens}"
        )

    if failures:
        print(f"Smoke test finished with {failures} failure(s).")
        return 1
    print("Smoke test passed for both roles.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
