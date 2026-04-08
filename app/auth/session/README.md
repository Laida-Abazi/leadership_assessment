# Session Management

Reusable session layer that works with **any** authentication method (Google OAuth, Magic Link, email/password). Sessions are stored in the database; JWTs carry `user_id` + `session_id` and are validated against the session store on every request.

## Features

- **Auth-agnostic**: After any successful login, call `create_session(user_id, request, db)` then issue a JWT with `create_access_token({"user_id": ..., "session_id": ...})`.
- **Per-device sessions**: Each login creates a new session (user_agent, ip_address, expires_at).
- **List sessions**: `GET /auth/sessions` returns all active sessions for the current user, with a `current` flag.
- **Revoke one**: `DELETE /auth/sessions/{session_id}` (user can only revoke their own).
- **Logout current**: `POST /auth/logout` revokes only the current session.
- **Logout all**: `POST /auth/logout-all` revokes all sessions for the current user.

## Requirements

Already in project `requirements.txt`:

- `fastapi`, `uvicorn`, `sqlalchemy[asyncio]`, `asyncpg`
- `python-jose[cryptography]`
- `pydantic`, `python-dotenv`

## Run

From the **project root**:

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Or:

```bash
python main.py
```

Ensure `.env` in the project root has `DATABASE_URL` (PostgreSQL) and optionally `JWT_SECRET_KEY`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `SESSION_EXPIRE_DAYS`.

## Example: Mock login then use session APIs

### 1. Login (get token)

```bash
curl -s -X POST http://localhost:8000/auth/mock-login \
  -H "Content-Type: application/json" \
  -d "{\"email\": \"you@example.com\"}"
```

Save the `access_token` from the response.

### 2. List sessions

```bash
curl -s http://localhost:8000/auth/sessions \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

### 3. Revoke a specific session

```bash
curl -s -X DELETE "http://localhost:8000/auth/sessions/SESSION_UUID" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

Use an `id` from the list-sessions response.

### 4. Logout (revoke current session only)

```bash
curl -s -X POST http://localhost:8000/auth/logout \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

### 5. Logout from all devices

```bash
curl -s -X POST http://localhost:8000/auth/logout-all \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

## Real login (email/password)

Same token shape; use the token from `POST /auth/login` with the session APIs above:

```bash
curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"email_or_username\": \"you@example.com\", \"password\": \"yourpassword\"}"
```

Then use the returned `access_token` in `Authorization: Bearer ...` for `GET /auth/sessions`, `POST /auth/logout`, etc.

## Code layout

- **auth.py**: `create_access_token(data)`, `decode_access_token(token)`, `get_current_session` dependency.
- **sessions.py**: `create_session(user_id, request, db)`, `list_active_sessions_for_user`, `revoke_session`, `revoke_all_sessions_for_user`, optional `cleanup_expired_sessions`.
- **main.py**: Routes for `/sessions`, `/sessions/{id}`, `/logout`, `/logout-all`, `/mock-login`.
- **schemas.py**: Pydantic models for API responses.

Session table: `sessions` (id, user_id, created_at, expires_at, revoked, user_agent, ip_address).
