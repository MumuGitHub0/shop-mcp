from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

from fastapi import Header

from .errors import ShopError


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return base64.b64encode(salt + digest).decode()


def verify_password(password: str, stored: str) -> bool:
    raw = base64.b64decode(stored)
    candidate = hashlib.scrypt(password.encode(), salt=raw[:16], n=2**14, r=8, p=1)
    return hmac.compare_digest(candidate, raw[16:])


def _secret() -> bytes:
    return os.getenv("SHOP_SECRET_KEY", "development-secret-change-me").encode()


def token_expire_seconds() -> int:
    return int(os.getenv("SHOP_TOKEN_EXPIRE_MINUTES", "60")) * 60


def create_token(user_id: str) -> str:
    payload = json.dumps(
        {"sub": user_id, "exp": int(time.time()) + token_expire_seconds()},
        separators=(",", ":"),
    ).encode()
    body = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    signature = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def decode_token_details(token: str) -> tuple[str, int]:
    """解析令牌，返回 (user_id, 过期时间戳)；无效或过期时抛出 ShopError。"""
    try:
        body, signature = token.split(".", 1)
        expected = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if payload["exp"] < time.time():
            raise ValueError
        return str(payload["sub"]), int(payload["exp"])
    except (KeyError, ValueError, json.JSONDecodeError):
        raise ShopError(401, "invalid_token", "无效或已过期的访问令牌") from None


def decode_token(token: str) -> str:
    user_id, _ = decode_token_details(token)
    return user_id


def create_confirmation_token(
    *, user_id: str, cart_fingerprint: str, shipping_address: str, points_total: int
) -> str:
    payload = json.dumps(
        {
            "sub": user_id,
            "cart_fingerprint": cart_fingerprint,
            "shipping_address": shipping_address,
            "points_total": points_total,
            "jti": base64.urlsafe_b64encode(os.urandom(18)).decode().rstrip("="),
            "exp": int(time.time()) + int(os.getenv("SHOP_CONFIRMATION_EXPIRE_SECONDS", "300")),
        },
        separators=(",", ":"),
    ).encode()
    body = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    signature = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def decode_confirmation_token(token: str) -> dict[str, object]:
    try:
        body, signature = token.split(".", 1)
        expected = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        required = {"sub", "cart_fingerprint", "shipping_address", "points_total", "jti", "exp"}
        if not required.issubset(payload) or payload["exp"] < time.time():
            raise ValueError
        return payload
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise ShopError(409, "invalid_confirmation_token", "确认令牌无效或已过期，请重新预览") from None


def bearer_token(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise ShopError(401, "authentication_required", "需要访问令牌")
    return authorization[7:]
