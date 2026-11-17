# Mission Control: Go/No-Go Launch Console

Engineers at the launch consoles file free-text anomaly reports, and a pipeline sorts, checks, and summarizes each one. The Flight Director sees the verdict, the review flags, the cost, and a trace of every step, then approves a drafted briefing.

## Quick start

Requirements: Python 3.11 or newer, and a macOS or Linux shell.

Start the app (sets everything up, then runs at http://127.0.0.1:8000):

```bash
./start.sh
```

Run all tests (no network and no API key needed):

```bash
./test.sh
```

If there is no `.env` file, `start.sh` creates one in demo mode, so the app runs without an API key.

- Console: http://127.0.0.1:8000/
- File a report: http://127.0.0.1:8000/file

## Using real models

1. Copy the example settings: `cp .env.example .env`
2. In `.env`, set:
   ```
   MODEL_PROVIDER=openai
   OPENAI_API_KEY=<your key>
   OPENAI_MODEL_FAST=gpt-6-luna
   OPENAI_MODEL_STRONG=gpt-6.1-terra
   ```
3. Run `./start.sh`

Demo mode (`MODEL_PROVIDER=fake`) returns fixed replies instead of calling a model, so the whole app can be tried without a key. The `.env` file is ignored by git and is never committed.

## How it works

Each report goes through these steps in order. Every step is recorded as a trace card in the console.

1. **Classify** (MODEL, fast role): sorts the report by subsystem and severity (high, medium, low). The reply must match a schema, and invalid values fall back safely: subsystem becomes `unknown` and severity becomes `high`.
2. **Check limits** (CODE): checks the readings against the launch limits and sets the verdict. Any reading outside its limit makes the verdict `no_go`.
   - Wind: 25 knots or less
   - Temperature: 4 C or more
   - Fuel: 98 to 100 percent
3. **Brief by** (CODE): sets how soon the Flight Director must be briefed. High is 5 minutes, medium is 15, and low is 30. Any limit violation counts as high.
4. **Three checks, at the same time** (MODEL, fast role): one looks for hidden instructions in the note, one for missing details, and one for panic in the tone. The hidden instruction check also runs a plain code pattern match. A flagged note is sent to review.
5. **Draft and review** (MODEL, strong role): one call drafts the briefing and a second call reviews it. Reviewer feedback goes into the next draft. The loop stops after at most 3 rounds.
6. **Human approval**: the briefing is saved as pending. Only a person can approve it, using the Approve button in the console.

Cost is calculated in code for each step from its token counts, and summed for each report.

## Design decisions

- **The verdict never comes from a model.** The verdict, violations, brief by time, and cost are all computed in plain Python in `app/rules.py`.
- **Model replies are never trusted as is.** Every reply is checked against a schema in code, with a safe fallback when it is invalid.
- **The model writes words, and code writes facts.** The model writes only the briefing prose. Code adds the verdict, violations, brief by time, and review flags on top, so a draft cannot misstate the verdict.
- **"Passed" and "approved" mean different things.** The reviewer model can only mark a draft passed. Only a person can approve a briefing.
- **Hidden instructions cannot change the outcome.** A note that tries to give the system instructions is flagged and sent to review. It has no effect on the verdict.
- **Checks fail safe.** If a check fails or returns an invalid reply, it is treated as flagged so a person looks at it.
- **Untrusted text is shown as plain text.** All report text in the console is inserted with `textContent`, so HTML or scripts inside a note never run.
- **Reports are saved before any model is called.** A model outage never loses an engineer's report.

## Rubric evidence

| Rubric item | Where to look |
|---|---|
| One command to start and one to test | This README (Quick start), `start.sh`, `test.sh` |
| Tool choices | `TOOL_CHOICES.md` |
| Filing reports | `app/static/file.html`, `app/static/file.js`, `app/schemas.py`, `tests/test_schemas.py`, `tests/test_reports_api.py`, `tests/test_restart.py` |
| Launch limits in plain code | `app/rules.py`, `tests/test_rules_limits.py` |
| Brief by time | `app/rules.py`, `tests/test_rules_brief_by.py`, `tests/test_pipeline.py` |
| Sorting with a model | `app/steps/classify.py`, `app/schemas.py`, `tests/test_classify.py` |
| Three checks and hidden instructions | `app/steps/checks.py`, `tests/test_checks.py`, `tests/test_hidden_instruction.py` |
| Draft, review, and approval | `app/steps/briefing.py`, `app/db.py`, `app/main.py`, `tests/test_briefing_loop.py`, `tests/test_approval.py` |
| Trace and cost | `app/static/console.js`, `app/pipeline.py`, `app/rules.py`, `tests/test_rules_cost.py`, `tests/test_pipeline.py`, `tests/test_ui.py` |
| Tests with no network or API key | `app/model_client.py` (`ModelClient`, `FakeModel`), `tests/conftest.py` |

## Project structure

```
mission-control/
  app/
    main.py            FastAPI app, routes, and approval endpoint
    schemas.py         Input and model reply schemas, clear error messages
    rules.py           Launch limits, brief by time, and cost (plain code)
    db.py              SQLite storage for reports, results, trace, briefings
    model_client.py    ModelClient interface, OpenAIClient, FakeModel
    pipeline.py        Runs every step in order and records the trace
    steps/
      classify.py      Subsystem and severity sorting
      checks.py        Three parallel checks and injection patterns
      briefing.py      Draft and review loop, briefing rendering
    static/
      index.html       Flight Director console
      file.html        Engineer report form
      console.js       Console logic, trace cards, approval
      file.js          Form logic and error messages
      common.js        Shared helpers
      styles.css       Shared styles
  tests/               All tests, run with ./test.sh
  scripts/
    smoke_model.py     Manual check of the real model connection
  start.sh             One command to start the app
  test.sh              One command to run the tests
  TOOL_CHOICES.md      Tool choices and reasons
```

## API

| Method | Route | Purpose |
|---|---|---|
| GET | `/` | Flight Director console |
| GET | `/file` | Engineer report form |
| POST | `/reports` | File a report and run the pipeline |
| GET | `/reports` | List reports, newest first |
| GET | `/reports/{id}` | One report with its result, briefing, and trace |
| POST | `/briefings/{id}/approve` | Approve a pending briefing (people only) |
| GET | `/health` | Health check |

Interactive API docs are available at http://127.0.0.1:8000/docs while the app is running.

## Known limitations

- SQLite stores data in one file on one machine. A multi-console setup would move to a server database. All database code lives in `app/db.py`.
- Demo mode returns fixed replies, so classifications and briefings look the same for every report.
- Model prices in `app/rules.py` are set for `gpt-6-luna` and `gpt-6.1-terra`. Update them if the models change.
