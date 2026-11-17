# Tool Choices

Mission Control has four main tool decisions. For each one, this file gives
the choice and the specific reason for it, one alternative I rejected, and
one concern a customer like Meridian Launch Services would raise, with how
the design handles it.

## Back end: Python with FastAPI

**Why:** The three checks must run at the same time. FastAPI is async by
default, so running them together is one call to `asyncio.gather`, and a
test proves all three are in flight at once. FastAPI also builds on
Pydantic, which gives two things this app needs: input validation with
clear error messages for the report form, and schema checking for every
model reply before the app trusts it. FastAPI's TestClient runs the whole
app in memory, which keeps the tests fast and offline.

**Rejected alternative: Flask.** Flask is simple and well known, but it is
synchronous by default and has no built-in validation. I would have needed
extra libraries for async calls and for schema checks, which adds code
without adding value here.

**Customer concern: can one Python process keep up during a launch?**
A launch produces a few dozen reports, and each report spends most of its
time waiting on model calls, which async handles well. If volume grew,
uvicorn can run several worker processes without code changes.

## Front end: plain HTML, CSS, and JavaScript, served by FastAPI

**Why:** The app has two screens: a form and a console. Plain files served
by the same FastAPI server mean one process, no build step, and a true
one-command start. It works offline, which suits console machines on a
locked-down network. All report text is inserted with `textContent`, so a
note containing HTML or script tags is shown as plain text and cannot run.

**Rejected alternative: React with Vite.** React would make a larger UI
easier to grow, but it needs Node.js, a second dev server, and a build
step. That is more moving parts than two screens justify, and it would
break the one-command start.

**Customer concern: will this be hard to extend later?** The front end only
displays what the API returns. It never calculates verdicts or cost. Since
all logic lives in the back end, the console could be rebuilt in a
framework later without touching the rules or the pipeline.

## Database: SQLite

**Why:** Reports must still be there after a restart. SQLite is built into
Python and stores everything in one file on disk, so there is nothing to
install or run. Each report's result, trace, and briefing are saved in a
single transaction, so a crash never leaves half a record. All queries use
parameters, so note text can never be run as SQL.

**Rejected alternative: PostgreSQL.** Postgres handles many users and
heavy concurrent writes, but it needs a separate server, credentials, and
setup on every machine. For one console used by one Flight Director, that
cost buys nothing.

**Customer concern: what about backups and multiple consoles?** SQLite is
one file on one machine, with no replication. For production, the
database code is isolated in `app/db.py`, so moving to Postgres means
changing that one module. Backups today are as simple as copying the file.

## Model: OpenAI API, two roles behind one interface

**Why:** The app makes 6 to 10 model calls per report, so I split the work
by difficulty:
- **Fast role (`gpt-6-luna`):** classification and the three checks. These
  are short, high-volume tasks with a fixed answer shape.
- **Strong role (`gpt-6.1-terra`):** drafting and reviewing the briefing,
  which needs better writing and judgment.

A worst-case report costs about 1 to 2 cents, calculated in code from
token counts. Every call goes through one `ModelClient` interface. Tests
use a `FakeModel` behind the same interface, so they need no network and
no API key, and the model names are set in `.env`, not in code.

**Rejected alternative: Claude Opus through the company's Azure Foundry
sandbox.** It was available, but it is one high-cost model, and using it
for every short classification and check call would multiply the cost per
report. It also uses a different request format, which would have meant a
separate client for no functional gain.

**Customer concern: does sending launch data to an outside API create
risk?** Three things limit the risk. First, no model ever decides the
verdict, the violations, the brief by time, or the cost; plain code does,
so a wrong or manipulated model reply cannot change a go or no go
decision. Second, only a person can approve a briefing. Third, because
every call goes through one interface, Meridian could point the app at a
model hosted inside its own cloud tenant by adding one client class and
changing `.env`, with no changes to the pipeline.