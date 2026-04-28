from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from jose import JWTError, jwt
from starlette.requests import HTTPConnection

from app.auth.login.service import ALGORITHM, SECRET_KEY

CANDIDATE_ACCESS_COOKIE_NAME = os.getenv("CANDIDATE_ACCESS_COOKIE_NAME", "candidate_access_token")
CANDIDATE_SESSION_EXPIRE_MINUTES = int(os.getenv("CANDIDATE_SESSION_EXPIRE_MINUTES", "720"))
_CANDIDATE_TOKEN_TYPE = "candidate_interview"


@dataclass(frozen=True)
class CandidateAccessContext:
    assessment_id: int
    link_id: int
    token_type: str = _CANDIDATE_TOKEN_TYPE


def create_candidate_session_token(*, assessment_id: int, link_id: int) -> str:
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=CANDIDATE_SESSION_EXPIRE_MINUTES)
    payload = {
        "sub": f"candidate:{assessment_id}",
        "token_type": _CANDIDATE_TOKEN_TYPE,
        "assessment_id": assessment_id,
        "link_id": link_id,
        "exp": expires_at,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_candidate_session_token(token: str) -> CandidateAccessContext:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        token_type = payload.get("token_type")
        if token_type != _CANDIDATE_TOKEN_TYPE:
            raise ValueError("Unexpected token type")
        assessment_id = int(payload.get("assessment_id"))
        link_id = int(payload.get("link_id"))
        if assessment_id <= 0 or link_id <= 0:
            raise ValueError("Invalid candidate claims")
        return CandidateAccessContext(
            assessment_id=assessment_id,
            link_id=link_id,
            token_type=token_type,
        )
    except (JWTError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired candidate session.",
        ) from exc


def get_candidate_access_context(connection: HTTPConnection) -> CandidateAccessContext:
    token = None
    authorization = connection.headers.get("authorization")
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if not token:
        token = connection.cookies.get(CANDIDATE_ACCESS_COOKIE_NAME)
    if not token:
        token = connection.query_params.get("candidate_token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Candidate session required.",
        )
    return decode_candidate_session_token(token)


def require_candidate_assessment_access(
    assessment_id: int,
    connection: HTTPConnection,
) -> CandidateAccessContext:
    context = get_candidate_access_context(connection)
    if context.assessment_id != assessment_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Candidate session does not match this assessment.",
        )
    return context
