from pydantic import BaseModel, EmailStr, Field

from app.models import Role


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=200)
    organization_name: str = Field(min_length=1, max_length=200)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class OrgBrief(BaseModel):
    id: int
    name: str
    slug: str
    role: Role


class MeOut(BaseModel):
    id: int
    email: str
    full_name: str | None
    organizations: list[OrgBrief]
