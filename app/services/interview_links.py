from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.db.models import AssessmentAccessLink, Assessments

INVALID_LINK_DETAIL = "Interview link unavailable."
_OPEN_ATTEMPTS: dict[str, deque[float]] = defaultdict(deque)


def one_time_interview_links_enabled() -> bool:
    return os.getenv("ENABLE_ONE_TIME_INTERVIEW_LINKS", "true").lower() not in {"0", "false", "no"}


def interview_link_ttl_hours() -> int:
    return int(os.getenv("INTERVIEW_LINK_TTL_HOURS", "48"))


def candidate_app_base_url() -> str:
    return os.getenv("APP_URL", "http://localhost:8000").rstrip("/")


def build_interview_link_url(raw_token: str, *, base_url: str | None = None) -> str:
    return f"{(base_url or candidate_app_base_url()).rstrip('/')}/candidate/interview/{raw_token}"


def build_candidate_fingerprint(ip_address: str | None, user_agent: str | None) -> str | None:
    raw = "|".join(part.strip() for part in [ip_address or "", user_agent or ""] if part and part.strip())
    if not raw:
        return None
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def enforce_link_open_rate_limit(client_key: str | None) -> None:
    if not client_key:
        return

    window_seconds = int(os.getenv("INTERVIEW_LINK_RATE_LIMIT_WINDOW_SECONDS", "60"))
    max_attempts = int(os.getenv("INTERVIEW_LINK_RATE_LIMIT_MAX_ATTEMPTS", "10"))
    now = time.monotonic()
    attempts = _OPEN_ATTEMPTS[client_key]

    while attempts and (now - attempts[0]) > window_seconds:
        attempts.popleft()

    if len(attempts) >= max_attempts:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many link attempts. Please try again shortly.",
        )

    attempts.append(now)


def parse_public_link_token(raw_token: str) -> tuple[int, str]:
    try:
        link_id_str, secret = raw_token.split(".", 1)
        link_id = int(link_id_str)
    except (AttributeError, TypeError, ValueError):
        raise_invalid_link()

    if link_id <= 0 or not secret:
        raise_invalid_link()
    return link_id, secret


def format_public_link_token(link_id: int, secret: str) -> str:
    return f"{link_id}.{secret}"


def compute_token_hash(secret: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{secret}".encode("utf-8")).hexdigest()


def issue_assessment_access_link(
    db: Session,
    *,
    assessment_id: int,
    created_by_user_id: int | None,
    candidate_email: str | None = None,
    issued_reason: str | None = None,
    ttl_hours: int | None = None,
    revoke_existing: bool = True,
) -> tuple[AssessmentAccessLink, str]:
    if not one_time_interview_links_enabled():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Interview links are disabled.")

    assessment = db.get(Assessments, assessment_id)
    if not assessment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assessment not found.")

    now = utcnow()
    expires_at = now + timedelta(hours=max(ttl_hours or interview_link_ttl_hours(), 1))

    try:
        if revoke_existing:
            active_links = (
                db.query(AssessmentAccessLink)
                .filter(
                    AssessmentAccessLink.assessment_id == assessment_id,
                    AssessmentAccessLink.revoked_at.is_(None),
                    AssessmentAccessLink.used_at.is_(None),
                    AssessmentAccessLink.expires_at > now,
                    AssessmentAccessLink.use_count < AssessmentAccessLink.max_uses,
                )
                .all()
            )
            for link in active_links:
                link.revoked_at = now
                link.updated_at = now

        secret = secrets.token_urlsafe(32)
        salt = secrets.token_hex(16)
        link = AssessmentAccessLink(
            assessment_id=assessment_id,
            created_by_user_id=created_by_user_id,
            token_hash=compute_token_hash(secret, salt),
            token_salt=salt,
            candidate_email=(candidate_email or "").strip() or None,
            issued_reason=(issued_reason or "").strip() or None,
            max_uses=1,
            use_count=0,
            expires_at=expires_at,
        )
        db.add(link)
        db.flush()
        raw_token = format_public_link_token(link.id, secret)
        db.commit()
        db.refresh(link)
        return link, raw_token
    except Exception:
        db.rollback()
        raise


def revoke_assessment_access_link(db: Session, link: AssessmentAccessLink) -> AssessmentAccessLink:
    now = utcnow()
    try:
        link.revoked_at = now
        link.updated_at = now
        db.add(link)
        db.commit()
        db.refresh(link)
        return link
    except Exception:
        db.rollback()
        raise


def get_latest_assessment_access_link(db: Session, assessment_id: int) -> AssessmentAccessLink | None:
    return (
        db.query(AssessmentAccessLink)
        .filter(AssessmentAccessLink.assessment_id == assessment_id)
        .order_by(AssessmentAccessLink.id.desc())
        .first()
    )


def get_link_status(link: AssessmentAccessLink | None) -> str | None:
    if link is None:
        return None
    now = utcnow()
    if link.used_at is not None or link.use_count >= link.max_uses:
        return "used"
    if link.revoked_at is not None:
        return "revoked"
    if link.expires_at <= now:
        return "expired"
    return "active"


def serialize_link_status(
    link: AssessmentAccessLink | None,
    *,
    include_url: bool = False,
    raw_token: str | None = None,
    base_url: str | None = None,
) -> dict | None:
    if link is None:
        return None

    payload = {
        "id": link.id,
        "assessment_id": link.assessment_id,
        "status": get_link_status(link),
        "candidate_email": link.candidate_email,
        "max_uses": link.max_uses,
        "use_count": link.use_count,
        "expires_at": link.expires_at.isoformat() if link.expires_at else None,
        "used_at": link.used_at.isoformat() if link.used_at else None,
        "revoked_at": link.revoked_at.isoformat() if link.revoked_at else None,
        "created_at": link.created_at.isoformat() if link.created_at else None,
    }
    if include_url and raw_token:
        payload["interview_link"] = build_interview_link_url(raw_token, base_url=base_url)
    return payload


def consume_assessment_access_link(
    db: Session,
    raw_token: str,
    *,
    fingerprint: str | None = None,
) -> AssessmentAccessLink:
    if not one_time_interview_links_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=INVALID_LINK_DETAIL)

    link_id, secret = parse_public_link_token(raw_token)
    now = utcnow()

    try:
        link = (
            db.query(AssessmentAccessLink)
            .filter(AssessmentAccessLink.id == link_id)
            .with_for_update()
            .first()
        )
        if not link:
            raise_invalid_link()

        expected_hash = compute_token_hash(secret, link.token_salt)
        if not hmac.compare_digest(expected_hash, link.token_hash):
            raise_invalid_link()

        if link.revoked_at is not None or link.expires_at <= now:
            raise_invalid_link()
        if link.used_at is not None or link.use_count >= link.max_uses:
            raise_invalid_link()

        link.use_count += 1
        link.used_at = now
        link.used_by_fingerprint = fingerprint
        link.updated_at = now
        db.add(link)
        db.commit()
        db.refresh(link)
        return link
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


def raise_invalid_link() -> None:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=INVALID_LINK_DETAIL)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
