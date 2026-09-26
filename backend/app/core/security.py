from datetime import timedelta
from typing import Literal

import bcrypt
import jwt

from app.core.config import settings
from app.core.db import utcnow

ALGORITHM = "HS256"
TokenType = Literal["access", "refresh"]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


def create_token(user_id: int, kind: TokenType) -> str:
    ttl = (timedelta(minutes=settings.access_token_minutes) if kind == "access"
           else timedelta(days=settings.refresh_token_days))
    now = utcnow()
    return jwt.encode({"sub": str(user_id), "type": kind, "iat": now, "exp": now + ttl},
                      settings.secret_key, algorithm=ALGORITHM)


def decode_token(token: str, kind: TokenType) -> int | None:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
    if payload.get("type") != kind:
        return None
    try:
        return int(payload["sub"])
    except (KeyError, ValueError):
        return None
