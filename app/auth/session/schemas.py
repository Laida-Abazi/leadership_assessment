from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class SessionOut(BaseModel):
    id: UUID
    created_at: datetime
    user_agent: str | None
    ip_address: str | None
    current: bool = False

    model_config = {"from_attributes": True}


class SessionListResponse(BaseModel):
    sessions: list[SessionOut]


class TokenPayload(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class MockLoginRequest(BaseModel):
    email: str


class MockLoginResponse(BaseModel):
    token: TokenPayload
    user_id: UUID
    session_id: UUID
