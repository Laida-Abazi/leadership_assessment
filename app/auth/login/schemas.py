from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1)


class UserBrief(BaseModel):
    id: int
    email: str
    name: str
    surname: str


class TokenPayload(BaseModel):
    token: str
    token_type: str = "bearer"


class LoginResponse(BaseModel):
    token: TokenPayload
    user: UserBrief
