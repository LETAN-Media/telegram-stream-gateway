"""Unit and integration tests for universal multi-site gateway features."""
import time
import pytest
from fastapi.testclient import TestClient

from app.config import ConfigError, Settings, load_settings, parse_allowed_chat_ids
from app.main import create_app
from app.security import build_signed_query, check_api_key, is_chat_allowed, sign_payload

SECRET = "super-secret-key-12345"


def test_allow_all_chats_setting():
    # Wildcard in ALLOWED_CHAT_IDS
    s = load_settings({"ALLOWED_CHAT_IDS": "*", "STREAM_SIGNING_SECRET": SECRET})
    assert s.allow_all_chats is True
    assert is_chat_allowed(-100999999, s.allowed_chat_ids, allow_all=s.allow_all_chats) is True

    # ALLOW_ALL_CHATS boolean
    s2 = load_settings({"ALLOW_ALL_CHATS": "true", "STREAM_SIGNING_SECRET": SECRET})
    assert s2.allow_all_chats is True

    # Default is false
    s3 = load_settings({"ALLOWED_CHAT_IDS": "-100123,-100456", "STREAM_SIGNING_SECRET": SECRET})
    assert s3.allow_all_chats is False
    assert is_chat_allowed(-100123, s3.allowed_chat_ids, allow_all=s3.allow_all_chats) is True
    assert is_chat_allowed(-100999, s3.allowed_chat_ids, allow_all=s3.allow_all_chats) is False


def test_check_api_key():
    assert check_api_key(SECRET, SECRET) is True
    assert check_api_key("wrong", SECRET) is False
    assert check_api_key(None, SECRET) is False
    assert check_api_key("", SECRET) is False


def test_cors_middleware():
    settings = Settings(
        api_id=None, api_hash=None, bot_token=None, session_string=None,
        allowed_chat_ids=frozenset({-100123}), signing_secret=SECRET,
        allow_unsigned=False, max_concurrent_streams=4, telegram_chunk_size=512 * 1024,
        request_timeout_seconds=30.0, port=8080, log_level="info",
        allow_all_chats=False,
    )
    app = create_app(settings)
    client = TestClient(app)

    # OPTIONS preflight request
    resp = client.options("/v1/stream/-100123/1", headers={
        "Origin": "https://any-other-site.com",
        "Access-Control-Request-Method": "GET",
    })
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") in ("*", "https://any-other-site.com")


def test_auth_methods():
    settings = Settings(
        api_id=None, api_hash=None, bot_token=None, session_string=None,
        allowed_chat_ids=frozenset({-100123}), signing_secret=SECRET,
        allow_unsigned=False, max_concurrent_streams=4, telegram_chunk_size=512 * 1024,
        request_timeout_seconds=30.0, port=8080, log_level="info",
        allow_all_chats=False,
    )
    app = create_app(settings)
    client = TestClient(app)

    # 1. Missing auth -> 401
    resp = client.get("/v1/video/-100123/1")
    assert resp.status_code == 401
    assert resp.json()["error"] == "unauthorized: missing api_key or exp/sig"

    # 2. Invalid API Key -> 401
    resp = client.get("/v1/video/-100123/1", headers={"X-API-Key": "wrong"})
    assert resp.status_code == 401

    # 3. Invalid exp / sig -> 403
    resp = client.get("/v1/video/-100123/1?exp=9999999999&sig=wrongsig")
    assert resp.status_code == 403

    # 4. Chat not allowed when not in allowlist
    resp = client.get("/v1/video/-100999/1?exp=9999999999&sig=abc")
    assert resp.status_code == 403
    assert resp.json()["error"] == "chat not allowed"

    # 5. Valid API Key grants access (falls through to telegram check 503 since tg not configured in fake test)
    resp = client.get("/v1/video/-100123/1", headers={"X-API-Key": SECRET})
    assert resp.status_code == 503  # Passed auth, reached telegram check!

    # 6. Bearer token in Authorization header
    resp = client.get("/v1/video/-100123/1", headers={"Authorization": f"Bearer {SECRET}"})
    assert resp.status_code == 503  # Passed auth!

    # 7. Query param api_key
    resp = client.get(f"/v1/video/-100123/1?api_key={SECRET}")
    assert resp.status_code == 503  # Passed auth!

    # 8. Valid HMAC signed query
    query = build_signed_query(-100123, 1, 3600, SECRET)
    resp = client.get(f"/v1/video/-100123/1?{query}")
    assert resp.status_code == 503  # Passed auth!
