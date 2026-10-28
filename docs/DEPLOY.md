# Deploying Jenius

## 1. Create the service

**Render (recommended):** New → Blueprint → choose this repository. [render.yaml](../render.yaml) creates the web
service and a PostgreSQL 17 database and connects them. Check the plan names against Render's current pricing
before you apply.

**Anywhere else:** build the [Dockerfile](../Dockerfile) and give it a PostgreSQL database. On start the
container runs `python -m jenius init-db` (migrations + demo data), then serves on `$PORT`.

## 2. Settings

| Setting | Value |
| --- | --- |
| `JENIUS_ENV` | `production` (insists on PostgreSQL, https and secure cookies) |
| `DATABASE_URL` | Your PostgreSQL URL. `postgres://…` URLs from Render, Railway, Neon or Supabase work as-is. |
| `PUBLIC_URL` | `https://your-domain`. On Render it defaults to the service's onrender.com address. |
| `LLM_PROVIDER` | `anthropic` for Claude, or `offline` for the free engine only |
| `ANTHROPIC_API_KEY` | Your Claude API key |
| `ANTHROPIC_MODEL` | `claude-opus-5-5` by default. `claude-sonnet-5-5` or `claude-haiku-5-5` cost less per answer. |
| `ANTHROPIC_EFFORT` | `medium` by default; `low` is cheaper and faster for simple questions |
| `FREE_DAILY_MESSAGES` / `PRO_DAILY_MESSAGES` | Claude messages per person per day (25 / 300) |
| `UPI_ID` | The UPI ID payments go to. The QR code and "Open in my UPI app" link are generated from it. |
| `PRO_MONTHLY_INR` / `PRO_ANNUAL_INR` | Prices in rupees (799 / 6708) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | For password-reset email (Resend, Postmark, SES, Brevo and Gmail all offer SMTP) |
| `SUPPORT_EMAIL` | Shown for Enterprise enquiries and on the privacy page |
| `FORWARDED_ALLOW_IPS` | `*` (the default in the container) when the app is only reachable through your host's proxy |
| `WEB_CONCURRENCY` | Server processes (2 by default) |

Every setting is checked at start-up; a wrong one stops the server with a message naming it.

## 3. First admin

Sign up on the live site, then run once in the host's shell (Render: Shell tab):

```bash
python -m jenius make-admin you@example.com
```

The Admin link appears in the menu. Approve a UPI payment only after you see its UTR number in your bank or
UPI app.

## 4. Before you announce it

- [ ] `https://your-domain/readyz` returns `{"ok": true, "database": "postgresql"}`
- [ ] Sign up, upload a CSV, ask a question, and check the Claude badge on the answer
- [ ] Request a password reset and receive the email
- [ ] Scan the UPI QR with your phone and check the payee and amount
- [ ] Set a monthly spend limit on your Anthropic account (Console → Limits)
- [ ] Turn on daily database backups (Render does this on paid PostgreSQL plans)
- [ ] Have the privacy page and your terms reviewed for your situation (India's DPDP Act applies to personal data)

## 5. Watching it

- `python -m jenius usage` or the Admin page: Claude calls, tokens and dollars per day
- Logs are JSON lines with a `request_id` you can match to the `X-Request-ID` header users see
- `/healthz` (process up) and `/readyz` (database reachable) are for your host's health checks

## Updating

Push to the deployed branch. The container applies new migrations on start. To change the schema: edit
`jenius/models.py`, then `alembic revision --autogenerate -m "what changed"`, review the file, and commit it.
