import uuid
from datetime import timedelta

import bcrypt
import jwt

from app.config import get_settings
from app.db import utcnow


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def _token(user_id: uuid.UUID, kind: str, ttl: timedelta) -> str:
    s = get_settings()
    now = utcnow()
    payload = {"sub": str(user_id), "typ": kind, "iat": now, "exp": now + ttl}
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256")


def make_tokens(user_id: uuid.UUID) -> tuple[str, str]:
    s = get_settings()
    return (
        _token(user_id, "access", timedelta(minutes=s.access_token_minutes)),
        _token(user_id, "refresh", timedelta(days=s.refresh_token_days)),
    )


def read_token(token: str, kind: str) -> uuid.UUID | None:
    # 만료는 앱 시계(clock)로 직접 판단한다. 서명은 PyJWT가 검사한다.
    try:
        payload = jwt.decode(
            token,
            get_settings().jwt_secret,
            algorithms=["HS256"],
            options={"verify_exp": False, "verify_iat": False, "require": ["exp", "sub", "typ"]},
        )
    except jwt.PyJWTError:
        return None
    if payload.get("typ") != kind or payload["exp"] <= utcnow().timestamp():
        return None
    try:
        return uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        return None
