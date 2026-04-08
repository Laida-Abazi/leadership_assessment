# Magic Link (passwordless) authentication

One-time login links sent by email. No password required.

## Flow

1. **POST /auth/magic_link/login-request** — User submits email → server generates a secure token, stores it (email + expiry + used flag), sends email with link.
2. **GET /auth/magic_link/magic-login?token=...** — User opens link → server verifies token (exists, not expired, not used), marks it used, authenticates user, returns success (e.g. user email; JWT/session can be added later).

## Run the server

From project root:

```bash
pip install -r requirements.txt
python auth/run.py
```

Server: **http://localhost:8000**

## Example curl requests

### 1. Request a magic link

```bash
curl -X POST http://localhost:8000/auth/magic_link/login-request \
  -H "Content-Type: application/json" \
  -d "{\"email\": \"you@example.com\"}"
```

**Success (200):**

```json
{
  "message": "If an account with that email exists (or you just signed up), we've sent you a login link. Check your inbox."
}
```

Check your email and use the link, or copy the `token` query parameter from the URL.

### 2. Log in with the token (magic-login)

```bash
curl "http://localhost:8000/auth/magic_link/magic-login?token=YOUR_TOKEN_FROM_EMAIL"
```

**Success (200):**

```json
{
  "email": "you@example.com"
}
```

### 3. Error cases

- **Missing or invalid token:**

```bash
curl "http://localhost:8000/auth/magic_link/magic-login?token=invalid"
# 400 {"detail":"Invalid or missing token."}
```

- **Expired token:** same endpoint after token has expired (e.g. > 12 minutes) → 400 `"This login link has expired. Please request a new one."`
- **Already used token:** calling magic-login again with the same token → 400 `"This login link has already been used. Please request a new one."`

## Configuration

| Env | Description |
|-----|-------------|
| `MAGIC_LINK_BASE_URL` | Base URL for the link in the email (default: `http://localhost:8000`) |
| Mail (see project root `.env`) | `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`, etc. |

## Security

- Token: `secrets.token_urlsafe(32)` (single-use, 12-minute expiry).
- Tokens stored in memory for demo; use a database table in production.
- Clear errors for invalid, expired, and already-used tokens.

## Extending to production

- Persist tokens in DB (e.g. `magic_link_tokens` table: token, email, expires_at, used).
- Add JWT (or session) in `MagicLinkSuccessResponse` and set cookie/header.
- Optionally rate-limit login-request by IP/email.
