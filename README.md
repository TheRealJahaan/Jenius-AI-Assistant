# Jenius

**Freight cost intelligence with an AI analyst that shows its work.**

Upload your shipment records. Jenius finds the route-weeks where cost per tonne-km jumped, checks each spike
against your operations notes (floods, festival surcharges, diesel prices…), and answers questions about your
data in plain English. Every number in an answer comes from your database, never from the model's imagination.

| | |
| --- | --- |
| **Anomaly engine** | Deterministic: a route-week is flagged only when it is ≥15% above both its own trailing 8-week average and same-type peer routes. Byte-identical output every run. |
| **AI assistant** | Claude with 7 read-only database tools (overview, anomalies, weekly costs, route comparison, carrier breakdown, shipments, note search). Cites notes like `[N001]`. |
| **Free fallback** | A built-in answer engine (no LLM, no cost) for visitors, for anyone over their daily quota, and whenever Claude is unreachable. |
| **Database** | PostgreSQL in production (SQLite on a laptop), with Alembic migrations. |
| **Accounts & billing** | Email + password (Argon2id), password reset by email, Free and Pro plans, UPI payments verified by an admin. |

The deterministic core started as the FreightTiger AI-intern case study; that write-up is in
[docs/CASE_STUDY.md](docs/CASE_STUDY.md).

## Quick start (your laptop)

You need Python 3.11 or newer.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env              # works as-is: SQLite + the free answer engine
python -m jenius init-db          # creates the tables and loads the public demo dataset
python -m jenius serve --reload   # http://127.0.0.1:8000
```

To use Claude, set `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY=...` in `.env` (get a key at
[platform.claude.com](https://platform.claude.com/settings/keys)).

**With Docker and PostgreSQL instead:**

```bash
docker compose up --build         # http://localhost:8000
```

## Going live

The repository deploys as one container plus a managed PostgreSQL database. The quickest route is Render:
**New → Blueprint → this repository** reads [render.yaml](render.yaml) and asks for your Claude key, UPI ID
and email settings. Any host that runs a Dockerfile works (Railway, Fly.io, a VPS); the container runs the
database migrations itself on start.

Read **[docs/DEPLOY.md](docs/DEPLOY.md)** before you launch: it is the checklist of settings, the first admin
account, backups and what to watch.

## How it works

```
Browser (web/: HTML + CSS + JS, strict CSP)
   │  JSON over HTTPS, session cookie (HttpOnly, SameSite=Lax)
   ▼
FastAPI app (jenius/api)
   ├─ auth        sign-up, log in, reset password, delete account
   ├─ datasets    CSV upload → validation → PostgreSQL → anomaly engine (jenius/analysis.py)
   ├─ chat        Claude agent loop (jenius/assistant/agent.py) ──► read-only tools on this dataset only
   │              or the free answer engine (jenius/assistant/offline.py)
   ├─ billing     UPI QR + transaction reference → admin approves → Pro
   └─ admin       pending payments, Claude cost per day
   ▼
PostgreSQL: users, sessions, datasets, shipments, context_notes, anomalies,
            conversations, messages, llm_calls (tokens + cost of every call), payments
```

Design choices worth knowing:

- **Claude can only read, and only the dataset in front of it.** The server binds every tool to one dataset;
  the model never chooses whose data it sees, so a malicious note in an upload can't reach anyone else.
- **Conversation history is append-only.** Each finished turn is stored exactly as Claude saw it (thinking
  blocks and tool calls included) and replayed unchanged. A failed turn is shown but never replayed.
- **The bill is protected.** Claude messages are counted per person per day in the database (25 on Free,
  300 on Pro by default); past the limit people get the free engine instead. Every call's tokens and cost are
  logged in `llm_calls`; `python -m jenius usage` and the admin page show them.
- **Refusals fall back automatically.** Requests use the API's server-side fallback (`fallbacks: "default"`)
  so a declined request is retried on Anthropic's recommended model.

## Day-to-day commands

| Command | What it does |
| --- | --- |
| `python -m jenius init-db` | Apply migrations and load the demo dataset (safe to re-run) |
| `python -m jenius serve` | Run the web app |
| `python -m jenius make-admin you@example.com` | Let an account approve payments and see usage |
| `python -m jenius payments` / `approve ID` / `reject ID` | Check UPI payments from the terminal |
| `python -m jenius usage` | Claude calls, tokens and cost per day |
| `python -m jenius ask "Why did Chennai-Bangalore get pricier?"` | Ask about the demo dataset from the terminal |
| `python solution.py` / `python eval_harness.py` | Regenerate and verify the case-study output |

## Upload format

**Shipments CSV** (required): `shipment_id, origin, destination, route_type, quantity_tonnes, distance_km,
freight_cost_inr, shipment_date` (YYYY-MM-DD), optionally `material, transporter`.
**Context notes CSV** (optional): `note_id, date, applies_to` (a route such as `Chennai-Bangalore`, or
`All Routes`), `note`. Bad rows are reported with their line numbers; nothing is silently skipped.

## Checks

```bash
ruff check .
python -m pytest                                                   # SQLite
JENIUS_TEST_DATABASE_URL=postgresql://... python -m pytest         # PostgreSQL
python eval_harness.py                                             # case-study reproducibility + accuracy
```

The tests need no API key: Claude is replaced by a scripted fake built from the SDK's own message types.
CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs all of the above against PostgreSQL 17 and
builds the container.

## Security

See [SECURITY.md](SECURITY.md).
