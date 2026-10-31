"""Authentication primitives for the local workbench."""

import base64
import hashlib
import hmac
import json
import os
import time

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.storage.app_db import get_user

_bearer = HTTPBearer(auto_error=False)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _secret() -> bytes:
    secret = os.getenv("APP_JWT_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError("APP_JWT_SECRET must contain at least 32 characters")
    return secret.encode()


def create_access_token(user: dict, expires_in: int = 60 * 60 * 12) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": str(user["id"]),
        "email": user["email"],
        "role": user["role"],
        "exp": int(time.time()) + expires_in,
    }
    encoded_header = _b64(json.dumps(header, separators=(",", ":")).encode())
    encoded_payload = _b64(json.dumps(payload, separators=(",", ":")).encode())
    unsigned = f"{encoded_header}.{encoded_payload}".encode()
    signature = _b64(hmac.new(_secret(), unsigned, hashlib.sha256).digest())
    return f"{encoded_header}.{encoded_payload}.{signature}"


def decode_access_token(token: str) -> dict:
    try:
        encoded_header, encoded_payload, signature = token.split(".", 2)
        unsigned = f"{encoded_header}.{encoded_payload}".encode()
        expected = _b64(hmac.new(_secret(), unsigned, hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid signature")
        payload = json.loads(_unb64(encoded_payload))
        if int(payload["exp"]) < int(time.time()):
            raise ValueError("expired token")
        return payload
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效或已过期的登录凭证") from exc


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    payload = decode_access_token(credentials.credentials)
    try:
        user_id = int(payload["sub"])
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的用户标识"
        ) from exc
    user = get_user(user_id)
    if not user or not user["is_active"]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已停用")
    return user


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user
