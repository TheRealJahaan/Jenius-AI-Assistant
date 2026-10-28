# Security

## Reporting a problem

Please don't open a public issue for security problems. Email the maintainer (the address in `SUPPORT_EMAIL`
on the live site) with what you found and how to reproduce it.

## What Jenius does to keep people's data safe

**Accounts**
- Passwords are hashed with Argon2id and checked against a short list of common passwords. Failed logins take
  the same time and give the same message whether or not the email exists.
- Sessions are 256-bit random tokens in an `HttpOnly`, `SameSite=Lax` cookie (`Secure` on https). Only a SHA-256
  hash is stored, so a copy of the database can't be used to sign in.
- Password-reset links are single-use, expire after one hour, sign the account out everywhere, and carry the
  token after `#` so it never reaches server logs.
- Login, sign-up, reset, upload, chat and payment endpoints are rate limited.

**Requests**
- State-changing requests from other websites are refused (Origin and `Sec-Fetch-Site` checks, plus SameSite
  cookies).
- Every response carries a strict Content-Security-Policy (`script-src 'self'`, no inline scripts),
  `X-Frame-Options: DENY`, `nosniff`, and HSTS on https. API docs are switched off in production.
- Errors never include stack traces; each response has an `X-Request-ID` to match it with the server log.

**Data**
- Every dataset is visible only to its owner (the demo is public and read-only). Other people's ids return 404.
- Uploads are size-limited, decoded as UTF-8, and validated row by row.
- Everything an upload or the AI produces is escaped before it is shown; the chat's small markdown renderer
  cannot create links, scripts or attributes.
- Deleting an account deletes its datasets, chats, sessions and payment records.

**The AI assistant**
- Claude's tools are read-only and bound by the server to the one dataset being discussed.
- The system prompt tells Claude that text from uploads is data, not instructions.
- Each person's Claude messages are capped per day, and every call's cost is logged.

**Payments**
- The browser never decides a price or a plan. A UPI payment stays `pending` until an admin matches the
  transaction reference to money received.

## Known limits

- Rate limits are kept in memory per server process. With several processes or machines the effective limit is
  multiplied; put Redis-backed limiting in front if you scale out. (The Claude quota is counted in the
  database and is exact.)
- Email addresses aren't verified at sign-up.
- UPI payments are checked by hand. Use a payment gateway with signed webhooks (for example Razorpay) before
  you take a high volume of payments.
