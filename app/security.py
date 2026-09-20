"""Signed-URL auth (HMAC-SHA256) + chat allowlist. Pure functions, fully tested."""
from __future__ import annotations

import hashlib
import hmac
import time


def sign_payload(chat_id: int, message_id: int, exp: int, secret: str) -> str:
    msg = f"{chat_id}:{message_id}:{exp}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def build_signed_query(chat_id: int, message_id: int, ttl_seconds: int, secret: str) -> str:
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be > 0")
    exp = int(time.time()) + ttl_seconds
    sig = sign_payload(chat_id, message_id, exp, secret)
    return f"exp={exp}&sig={sig}"


def verify_signature(
    chat_id: int,
    message_id: int,
    exp: int,
    sig: str,
    secret: str,
    now: float | None = None,
) -> bool:
    if exp <= 0 or not sig:
        return False
    current = time.time() if now is None else now
    if exp <= int(current):
        return False
    expected = sign_payload(chat_id, message_id, exp, secret)
    return hmac.compare_digest(expected, sig.lower().strip())


def is_chat_allowed(chat_id: int, allowed: frozenset[int]) -> bool:
    return chat_id in allowed
