# StickerHub Security Guide

Security controls implemented in this project and guardrails for future changes.

## Authentication and sessions

- **Web sessions** use Flask-Login with `HttpOnly`, `Secure` (production), and `SameSite=Lax` cookies.
- **API auth** stores JWT access and refresh tokens in **HttpOnly cookies**, not `localStorage` or `sessionStorage`.
- **Token rotation**: call `POST /api/auth/refresh` to rotate access and refresh tokens.
- **Session invalidation**: logout and MFA changes bump `token_version` so old tokens stop working.
- **MFA (TOTP)**: optional at `/dashboard/security` (web) or `/api/auth/mfa/*` (API).

### API clients using cookie auth

When calling protected API routes from JavaScript on the same origin:

1. Log in via `POST /api/auth/login` (credentials included).
2. Read the CSRF token from the `csrf_access_token` cookie.
3. Send it as header `X-CSRF-TOKEN` on mutating requests.

Do **not** persist JWT strings in browser storage.

## Access control

- Sticker edit/delete routes verify **resource ownership** (`uploader_id`).
- Public feed endpoints never expose uploader identity.
- Open redirects blocked on login `next` parameter.

## Input validation

- Usernames, emails, and passwords validated server-side.
- Titles and tags sanitized with `bleach` (HTML stripped).
- Uploads verified by **magic bytes** and Pillow image parsing, not just file extension.
- SQL access uses SQLAlchemy ORM (parameterized queries).

## Infrastructure

- Secrets live in environment variables only (`.env` locally, Vercel/host dashboard in production).
- Never commit `.env` or API keys to git.
- Set `RATELIMIT_STORAGE_URI` to Redis in production for consistent rate limits across instances.

## Security headers

Applied on every response:

- `Content-Security-Policy`
- `X-Frame-Options: DENY`
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy`
- `Strict-Transport-Security` (production)

## Rate limiting

| Endpoint | Limit |
|---|---|
| Login | 10 / minute |
| Register | 5 / hour |
| Upload | 20 / hour |
| Public feed | 120 / minute |

## Pre-deploy checks

```powershell
pip install -r requirements.txt
python scripts/security_check.py
# or
flask --app main security-check
```

This scans for hardcoded secrets and runs `pip-audit` on dependencies.

## AI / vibe-coding guardrails

Before shipping new features, verify:

1. No secrets in code, templates, or client JS.
2. Every new route checks authorization for the **specific resource**, not just login status.
3. All forms include CSRF tokens; API cookie auth includes JWT CSRF headers.
4. User input is validated and sanitized on the **server**, even if the UI validates too.
5. File uploads check content type, size, and binary signature.
6. Run `python scripts/security_check.py` before deploy.

## Auth providers (Auth0 / Firebase)

The app uses hardened first-party auth (werkzeug password hashing, MFA, secure cookies). To migrate to Auth0 or Firebase later:

1. Replace `/api/auth/login` and web login with the provider OAuth/OIDC flow.
2. Keep resource-level authorization and input validation unchanged.
3. Store provider tokens only in HttpOnly cookies or server-side sessions.
