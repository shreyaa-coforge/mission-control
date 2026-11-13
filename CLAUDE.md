# Mission Control: Go/No-Go Launch Console

Stack: Python 3.11+, FastAPI, SQLite, plain HTML/JS front end served by FastAPI,
Azure OpenAI via the official `openai` package, pytest for tests.

## Permanent project rules

- The launch verdict, violations, brief by time, and cost are computed in plain
  Python in `app/rules.py`. Never use a model for these.
- Launch limits: `wind_knots <= 25`, `temp_c >= 4`, `98 <= fuel_pct <= 100`.
  Any reading outside its limit makes the verdict `"no_go"`.
- Brief by: high = 5, medium = 15, low = 30 minutes. Any violation forces high.
- Every model call goes through one `ModelClient` interface in
  `app/model_client.py`. Tests use a `FakeModel` and must never need network
  access or an API key.
- Nothing in the pipeline may ever approve a briefing. Only the
  `POST /briefings/{id}/approve` route, triggered by a human, can approve.
- Model output must never change the verdict.
- Every new feature gets tests in `tests/`.
- Never commit `.env` or real keys.
