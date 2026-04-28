# Leadership Assessment

## Database migrations

Migrations live in `app/db/migrations/versions/`. Run from the **project root**:

```bash
# Apply all migrations
alembic -c alembic.ini upgrade head

# Create a new migration (after changing models)
alembic -c alembic.ini revision --autogenerate -m "description"

# Roll back one revision
alembic -c alembic.ini downgrade -1

# Show current revision
alembic -c alembic.ini current
```

Uses `DATABASE_URL` from `app/.env` when set.

---

## RAG (embeddings for the conversation agent)

Requirements and assessment questions are stored as embeddings so the agent can retrieve relevant context during the interview.

### Where it’s used

- **Indexing**: After an assessment is generated, `index_assessment` is called in **`app/as_blueprinting/routes/assessment.py`** (in the `POST /assessments/generate` flow). That embeds the job requirements and all 10 question types and writes them to `assessment_context_embeddings`.
- **Retrieval**: The conversation agent should call **`get_context_for_agent(db, query_text, assessment_id=..., limit=15)`** (from `app/rag/embeddings.py`) with the current user message (or last few messages) to get relevant requirements and questions. Until the agent is built, you can use the **`POST /assessments/context`** endpoint to test retrieval.

### How to test

1. **Apply migrations** (includes the embeddings table and pgvector):
   ```bash
   alembic -c alembic.ini upgrade head
   ```

2. **Create an assessment** (this also runs indexing):
   ```bash
   curl -X POST http://localhost:8000/assessments/generate \
     -H "Content-Type: application/json" \
     -d '{"job_requirements_id": 1, "user_id": 1}'
   ```
   Use a real `job_requirements_id` and `user_id` from your DB.

3. **Test retrieval via API**:
   ```bash
   curl -X POST http://localhost:8000/assessments/context \
     -H "Content-Type: application/json" \
     -d '{"assessment_id": 1, "query": "How do you handle conflict?", "limit": 5}'
   ```
   Replace `1` with the assessment id returned in step 2.

4. **Test with the script** (from project root; needs `OPENAI_API_KEY` and `DATABASE_URL`):
   ```bash
   PYTHONPATH=. python app/scripts/test_rag.py --assessment-id 1
   PYTHONPATH=. python app/scripts/test_rag.py --assessment-id 1 --query "Tell me about leadership"
   PYTHONPATH=. python app/scripts/test_rag.py --assessment-id 1 --reindex
   ```

---

## Endpoints Added Or Updated On 2026-04-28

No commits were created today; this section documents the endpoints currently present in today's working tree changes.

### Authenticated application APIs

These routers are mounted in `app/run.py` with `require_authenticated_user`, so they require an authenticated user session.

#### Job requirements

- `POST /job-requirements/analyze`
  - Accepts `multipart/form-data` with `job_description`.
  - Uses the LLM extractor to normalize the job description into one `job_requirements` row and returns the created record.

- `POST /job-requirements/analyze-linkedin-url`
  - Accepts JSON with `linkedin_job_url`.
  - Fetches a LinkedIn posting through Bright Data, extracts structured requirements, stores them, and returns the created row plus LinkedIn source metadata.

#### Assessments

- `POST /assessments/generate`
  - Accepts JSON with `job_requirements_id`, `user_id`, `assessment_type_code`, and optional interview-link settings.
  - Generates an assessment from an existing job requirements record, persists normalized assessment items, indexes the assessment for RAG, and optionally issues a one-time candidate interview link.

- `POST /assessments/generate-from-linkedin-job`
  - Accepts JSON with `linkedin_job_url`, `user_id`, `assessment_type_code`, and optional interview-link settings.
  - Pulls the LinkedIn job via Bright Data, creates the job requirements record, generates the assessment, indexes it, and can immediately issue a one-time candidate link.

- `POST /assessments/{assessment_id}/invite-link`
  - Accepts JSON with optional `candidate_email`, `issued_reason`, and `ttl_hours`.
  - Creates a new one-time interview link for the assessment and returns link status plus the generated interview URL.

- `POST /assessments/{assessment_id}/invite-link/revoke`
  - Revokes the latest interview link for the assessment and returns the updated link status.

- `GET /assessments/{assessment_id}/invite-link/status`
  - Returns the latest interview-link status for the assessment.

- `POST /assessments/context`
  - Accepts JSON with `assessment_id`, `query`, and optional `limit`.
  - Returns RAG context chunks for the assessment so the voice agent or test tooling can retrieve relevant requirements and question context.

- `GET /assessments/user/{user_id}/overview`
  - Returns grouped assessment overviews for the current authenticated user, including linked job requirements, latest analysis, and latest interview-link status.
  - Rejects access if `user_id` does not match the current user.

#### Intelligence

- `GET /intelligence/assessment/{assessment_id}/segments`
  - Returns saved `response_segments` for the assessment ordered by `sequence_order`.

- `GET /intelligence/assessment/{assessment_id}/signals`
  - Returns extracted `response_signals` grouped by response type.

- `GET /intelligence/assessment/{assessment_id}/analysis`
  - Returns the final stored analysis payload for the assessment, including aggregated traits, consistency scores, gaps, contradictions, behavioral patterns, and latest assessment result snapshot when available.

- `GET /intelligence/assessment/{assessment_id}/predictions`
  - Returns the final predictions payload for the assessment, including hiring recommendation, fit score, confidence score, risk flags, and type-specific result data when available.

- `GET /intelligence/assessment/{assessment_id}/status`
  - Returns the current intelligence-processing status for the assessment.

- `POST /intelligence/assessment/{assessment_id}/rerun`
  - Manually re-dispatches final analysis for an assessment using already collected segments and signals.
  - Returns `202 Accepted`; if a rerun is already in progress it returns an accepted response describing that state.

### Candidate interview APIs

These routes are mounted without admin auth, but they are scoped to one-time interview access.

- `GET /candidate/interview/{raw_token}`
  - Public entry point for a one-time candidate interview link.
  - Consumes the raw access token, applies a link-open rate limit, creates a candidate session cookie, and redirects to the session page.

- `GET /candidate/interview/session?assessment_id={assessment_id}`
  - Requires a valid candidate assessment-access session.
  - Serves the candidate interview HTML page for the requested assessment.

- `GET /candidate/assessment/{assessment_id}/status`
  - Requires candidate access for that assessment.
  - Returns the same intelligence-processing status payload exposed to admins.

- `GET /candidate/assessment/{assessment_id}/analysis`
  - Requires candidate access for that assessment.
  - Returns the assessment's analysis payload.

- `GET /candidate/assessment/{assessment_id}/predictions`
  - Requires candidate access for that assessment.
  - Returns the assessment's predictions payload.

### Agent endpoints

- `GET /agent/test`
  - Serves the local HTML test page for the voice agent.

- `GET /agent/ws/docs`
  - Returns Swagger-visible documentation for the WebSocket integration, including auth rules, query params, and message contracts.

- `WS /agent/ws`
  - WebSocket endpoint for the Gemini Live voice interview/conversation flow.
  - Accepts either an authenticated admin session or a valid candidate interview session.
  - Supports optional `assessment_id` for structured interview mode and optional `candidate_token` when cookie-based candidate auth is not available.

### Utility and local UI endpoints

- `GET /health`
  - Basic health check that returns `{"status": "ok"}`.

- `GET /test/pipeline`
  - Serves the end-to-end local pipeline test UI.

- `GET /auth/login`
  - Serves the login HTML page.

- `GET /auth/signup`
  - Serves the signup HTML page.

---

## Agent WebSocket API (for frontend integration)

Use this endpoint for custom frontend UI integrations:

- **WebSocket URL**: `ws://localhost:8000/agent/ws`
- **Production URL pattern**: `wss://<your-domain>/agent/ws`

### Authentication

Pass a valid access token as a query parameter:

`/agent/ws?access_token=<JWT>`

If the token is missing, invalid, expired, or not verified, the socket is closed with policy violation (`1008`).

### Optional query params

- `assessment_id` (int): when set, the agent runs the structured interview flow and saves answers.

Example:

`/agent/ws?access_token=<JWT>&assessment_id=123`

### Message contract

- **Client -> server**
  - Binary frames: raw PCM audio (16-bit, mono, 16kHz).
  - Text frames (JSON): control/events from client UI.
- **Server -> client**
  - Binary frames: raw PCM audio (16-bit, mono, 24kHz).
  - Text frames (JSON): status/events (for example `status`, `warning`, `error`, `interrupted`, `answers_saved`).

### Local test UI

`/agent/test` serves the built-in local HTML test page.  
It is intended only for local testing, not for third-party frontend integration.
