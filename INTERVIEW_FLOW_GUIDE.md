# Interview Flow Guide

This guide explains the full interview flow for the assessment system, from link generation to the candidate experience and final results retrieval. It is written for a React frontend integration.

## Overview

The interview system has two main sides:

1. Admin flow: create an assessment, issue a one-time interview link, monitor link status, and review candidates/results.
2. Candidate flow: open the invite link, submit identity details, start the live interview, then wait for analysis and prediction results.

The backend is currently a mix of JSON APIs and backend-rendered candidate pages:

- Admin endpoints are standard JSON APIs.
- Candidate link opening and session bootstrapping are currently HTML/form/redirect based.
- The actual interview itself runs over WebSocket.

## End-to-End Call Order

### 1. Create the assessment

Use one of these two endpoints:

#### `POST /assessments/generate`

Use this when you already have a `job_requirements_id`.

Request body:

```json
{
  "job_requirements_id": 123,
  "user_id": 45,
  "assessment_type_code": "leadership_core",
  "issue_one_time_link": true,
  "link_ttl_hours": 48,
  "candidate_email": "candidate@example.com",
  "issued_reason": "Initial invite"
}
```

#### `POST /assessments/generate-from-linkedin-job`

Use this when you want the backend to fetch a LinkedIn job and generate the assessment from that.

Request body:

```json
{
  "linkedin_job_url": "https://www.linkedin.com/jobs/view/...",
  "user_id": 45,
  "assessment_type_code": "leadership_core",
  "issue_one_time_link": true,
  "link_ttl_hours": 48,
  "candidate_email": "candidate@example.com",
  "issued_reason": "Initial invite"
}
```

What these endpoints do:

- create the assessment row
- generate the assessment items/questions
- optionally issue a one-time interview link

What they return:

- assessment metadata
- `items` array containing the question definitions
- `latest_interview_link` when `issue_one_time_link=true`

Typical response:

```json
{
  "id": 99,
  "user_id": 45,
  "job_requirements_id": 123,
  "assessment_type_code": "leadership_core",
  "assessment_version": "...",
  "items": [
    {
      "item_key": "behavioral_question",
      "display_label": "Behavioral",
      "item_order": 1,
      "item_kind": "question",
      "prompt_text": "Tell me about a time..."
    }
  ],
  "latest_interview_link": {
    "id": 7,
    "assessment_id": 99,
    "status": "active",
    "candidate_email": "candidate@example.com",
    "max_uses": 1,
    "use_count": 0,
    "expires_at": "2026-04-30T12:00:00+00:00",
    "used_at": null,
    "revoked_at": null,
    "created_at": "2026-04-30T10:00:00+00:00",
    "interview_link": "https://your-backend/candidate/interview/7.secret"
  }
}
```

When to call:

- recruiter/admin creates a new interview assessment

### 2. Issue or reissue an interview link

If the assessment already exists and you want to send or resend a candidate link:

#### `POST /assessments/{assessment_id}/invite-link`

Request body:

```json
{
  "candidate_email": "candidate@example.com",
  "issued_reason": "Resent invite",
  "ttl_hours": 48
}
```

What it does:

- creates a new one-time interview link
- revokes existing active unused links for that assessment

Returns:

```json
{
  "id": 7,
  "assessment_id": 99,
  "status": "active",
  "candidate_email": "candidate@example.com",
  "max_uses": 1,
  "use_count": 0,
  "expires_at": "2026-04-30T12:00:00+00:00",
  "used_at": null,
  "revoked_at": null,
  "created_at": "2026-04-30T10:00:00+00:00",
  "interview_link": "https://your-backend/candidate/interview/7.secret"
}
```

When to call:

- admin clicks "Send invite"
- admin clicks "Resend invite"

### 3. Check invite link status

#### `GET /assessments/{assessment_id}/invite-link/status`

What it does:

- returns the latest link for the assessment and its current state

Possible `status` values:

- `active`
- `used`
- `revoked`
- `expired`

Typical response:

```json
{
  "id": 7,
  "assessment_id": 99,
  "status": "used",
  "candidate_email": "candidate@example.com",
  "max_uses": 1,
  "use_count": 1,
  "expires_at": "2026-04-30T12:00:00+00:00",
  "used_at": "2026-04-30T10:30:00+00:00",
  "revoked_at": null,
  "created_at": "2026-04-30T10:00:00+00:00",
  "interview_link": null
}
```

When to call:

- admin assessment details screen
- admin dashboard refresh

### 4. Revoke a link

#### `POST /assessments/{assessment_id}/invite-link/revoke`

What it does:

- revokes the latest interview link for the assessment

When to call:

- admin explicitly cancels an invite

### 5. Candidate opens the invite link

#### `GET /candidate/interview/{raw_token}`

Example:

`/candidate/interview/7.secret`

What it does:

- validates the link
- checks that it is still available
- does not consume it yet
- returns an HTML candidate identity form
- rate-limits repeated link opens from the same IP

What it returns:

- HTML page, not JSON

Failure cases:

- `404` if link is invalid, already used, revoked, or expired
- `429` if too many repeated open attempts happen in a short window

When to call:

- user clicks the interview link from email
- this is usually a browser navigation, not a React API fetch

### 6. Candidate submits identity and begins the interview

#### `POST /candidate/interview/{raw_token}/begin`

Expected form fields:

- `first_name`
- `last_name`
- `email`

What it does:

- validates the candidate information
- consumes the one-time interview link
- creates the candidate record
- creates a candidate session token
- stores that token in an `HttpOnly` cookie
- returns a `303` redirect to the interview session page

What it returns:

- HTTP redirect to `/candidate/interview/session?assessment_id={assessment_id}`
- `Set-Cookie` header for the candidate session

Important:

- this is the point where the link becomes used
- submitting this twice with the same link will fail

### 7. Candidate session page

#### `GET /candidate/interview/session?assessment_id={assessment_id}`

What it does:

- checks candidate session access
- serves the candidate interview UI shell

What it returns:

- HTML page, not JSON

### 8. Start the live interview over WebSocket

#### `WS /agent/ws?assessment_id={assessment_id}`

What it does:

- starts the structured interview
- reads the candidate session from cookie, bearer token, or query param
- streams voice/audio between client and backend
- saves answers while the interview is happening
- triggers final analysis automatically once all questions are answered

Authentication methods accepted by the backend:

- candidate session cookie
- `Authorization: Bearer <candidate_session_token>`
- `?candidate_token=<candidate_session_token>`

Audio contract:

- client to server: raw PCM audio, 16-bit mono, 16kHz
- server to client: raw PCM audio, 16-bit mono, 24kHz

Server text events you should handle:

```json
{ "status": "connected" }
```

```json
{ "status": "reconnected" }
```

```json
{ "question_index": 2, "total_questions": 10 }
```

```json
{ "answers_saved": true }
```

```json
{ "error": "..." }
```

How to interpret those:

- `connected` or `reconnected`: the session is live and the candidate can speak
- `question_index` and `total_questions`: update progress UI
- `answers_saved`: the interview is finished and backend processing has started
- `error`: show a failure state to the candidate

When to open the socket:

- after the candidate reaches the interview session page and presses "Start Interview"

### 9. Poll interview processing status

Once the WebSocket signals completion with `answers_saved: true`, start polling:

#### `GET /candidate/assessment/{assessment_id}/status`

Typical response:

```json
{
  "assessment_id": 99,
  "status": "pending",
  "pending_signal_tasks": 2,
  "segment_count": 8,
  "signal_count": 6,
  "signals_ready": false,
  "analysis_running": false,
  "analysis_ready": false,
  "prediction_ready": false,
  "failed": false,
  "attempts": 1,
  "started_at": "2026-04-30T10:31:00+00:00",
  "completed_at": null,
  "updated_at": "2026-04-30T10:31:10+00:00",
  "last_error": null
}
```

Meaning of important fields:

- `status`: high-level pipeline state
- `pending_signal_tasks`: how many signal-extraction tasks are still running
- `segment_count`: how many response segments were saved
- `signal_count`: how many segments already have extracted signals
- `analysis_running`: final analysis currently running
- `analysis_ready`: analysis record exists
- `prediction_ready`: prediction record exists
- `failed`: pipeline failed
- `last_error`: useful for surfacing backend processing issues

Recommended polling rule:

- poll every 2 to 3 seconds
- stop polling when `status` becomes `completed` or `failed`

Common status interpretations:

- `pending`: answers exist but extraction/processing is still happening
- `running`: final analysis is actively running
- `completed`: final outputs are ready
- `failed`: something went wrong during processing

### 10. Fetch final analysis

#### `GET /candidate/assessment/{assessment_id}/analysis`

What it returns:

```json
{
  "id": 12,
  "assessment_id": 99,
  "job_requirements_id": 123,
  "assessment_type_code": "leadership_core",
  "analysis_text": "Detailed fits/gaps narrative...",
  "aggregated_traits": {
    "stakeholder_management": 0.8
  },
  "consistency_scores": {},
  "trait_gaps": {},
  "contradictions": {},
  "behavioral_patterns": {},
  "assessment_result": {
    "shared_result": {},
    "type_result": {},
    "narrative": "Narrative snapshot",
    "fit_score": 0.82,
    "confidence_score": 0.77,
    "risk_flags": ["minor risk"]
  }
}
```

When to call:

- after `GET /candidate/assessment/{assessment_id}/status` reports `completed`

### 11. Fetch final recommendation/predictions

#### `GET /candidate/assessment/{assessment_id}/predictions`

What it returns:

```json
{
  "id": 22,
  "analysis_id": 12,
  "assessment_type_code": "leadership_core",
  "hiring_recommendation": "Strong hire recommendation.",
  "fit_score": 0.82,
  "confidence_score": 0.77,
  "risk_flags": ["minor risk"],
  "type_result": {}
}
```

When to call:

- after status becomes `completed`

## Admin-Side Supporting Endpoint

### List candidates for an assessment

#### `GET /assessments/candidates?assessment_id={assessment_id}`

What it does:

- lists candidate rows for the current authenticated admin user
- optionally filters to a single assessment
- includes result snapshots when available

Typical response:

```json
[
  {
    "id": 1,
    "assessment_id": 99,
    "access_link_id": 7,
    "first_name": "Ana",
    "last_name": "Smith",
    "email": "ana@example.com",
    "assessment_type_code": "leadership_core",
    "analysis": "Candidate shows strong ownership...",
    "prediction": "Strong hire recommendation.",
    "fit_score": 0.82,
    "confidence_score": 0.77,
    "risk_flags": ["minor risk"],
    "link_token": "7.secret",
    "link_created_at": "2026-04-30T10:00:00+00:00",
    "link_expires_at": "2026-04-30T12:00:00+00:00",
    "last_result_sync_at": "2026-04-30T10:35:00+00:00",
    "created_at": "2026-04-30T10:20:00+00:00",
    "updated_at": "2026-04-30T10:35:00+00:00"
  }
]
```

Use this for:

- admin candidate tracking screen
- seeing who has used the link
- showing summary results after completion

## Recommended React Integration

## Admin flow

Suggested order in your React app:

1. Create the assessment with either `POST /assessments/generate` or `POST /assessments/generate-from-linkedin-job`
2. If the link was not created there, call `POST /assessments/{assessment_id}/invite-link`
3. Display or send the returned `interview_link`
4. Use `GET /assessments/{assessment_id}/invite-link/status` to show whether the link is active, used, revoked, or expired
5. Use `GET /assessments/candidates?assessment_id={assessment_id}` to show candidate progress and result snapshots

## Candidate flow

As the backend works today, the candidate path is:

1. Browser navigates to `/candidate/interview/{raw_token}`
2. Candidate submits identity to `POST /candidate/interview/{raw_token}/begin`
3. Backend sets candidate session cookie and redirects to `/candidate/interview/session?assessment_id=...`
4. Candidate page opens `WS /agent/ws?assessment_id=...`
5. When the socket sends `{ "answers_saved": true }`, start polling `/candidate/assessment/{assessment_id}/status`
6. When status is `completed`, fetch analysis and predictions

## Important frontend note

The candidate flow is not fully JSON-first yet.

Right now:

- `GET /candidate/interview/{raw_token}` returns HTML
- `POST /candidate/interview/{raw_token}/begin` expects form fields
- that begin endpoint sets an `HttpOnly` cookie and redirects
- it does not currently return the candidate session token in JSON

This matters if your React frontend is on a different origin/domain from the backend.

The backend candidate auth layer can read candidate auth from:

- cookie
- bearer token
- `candidate_token` query param

But the current bootstrapping flow only exposes that token through the cookie path. If you want a cleaner React SPA integration for the candidate experience, a useful backend enhancement would be a JSON alternative such as:

`POST /candidate/interview/{raw_token}/begin.json`

Response:

```json
{
  "assessment_id": 99,
  "candidate_token": "..."
}
```

That would let your React app:

- submit candidate identity as JSON
- receive a candidate token
- open `WS /agent/ws?assessment_id=99&candidate_token=...`
- call candidate status, analysis, and predictions with bearer auth or `candidate_token`

Until that exists, the simplest approach is:

- use React for the admin side
- keep the candidate entry/session pages served by the backend

## Practical frontend rules

- Use `credentials: "include"` when you rely on cookie-based authentication.
- Treat interview links as strictly one-time use.
- Do not fetch analysis and predictions immediately after the interview ends; poll status first.
- Stop polling once status is `completed` or `failed`.
- If a new invite link is issued, assume the old active link is no longer valid.

## Backend files that implement this flow

- `app/as_blueprinting/routes/assessment.py`
- `app/routers/candidate.py`
- `app/services/interview_links.py`
- `app/services/assessment_candidates.py`
- `app/auth/candidate_access.py`
- `app/agent/routes.py`
- `app/routers/intelligence.py`
- `app/services/intelligence.py`
