"""Unit tests: HMAC signature, expiry, chat allowlist, config guards."""
import time

import pytest

from app.config import ConfigError, load_settings, parse_allowed_chat_ids
from app.security import build_signed_query, is_chat_allowed, sign_payload, verify_signature

SECRET = "test-secret-for-unit-tests-only"


def test_sign_verify_roundtrip():
    exp = int(time.time()) + 3600
    sig = sign_payload(-100123, 25, exp, SECRET)
    assert verify_signature(-100123, 25, exp, sig, SECRET)


def test_tampered_message_id_fails():
    exp = int(time.time()) + 3600
    sig = sign_payload(-100123, 25, exp, SECRET)
    assert not verify_signature(-100123, 26, exp, sig, SECRET)


def test_tampered_chat_fails():
    exp = int(time.time()) + 3600
    sig = sign_payload(-100123, 25, exp, SECRET)
    assert not verify_signature(-100124, 25, exp, sig, SECRET)


def test_wrong_secret_fails():
    exp = int(time.time()) + 3600
    sig = sign_payload(-100123, 25, exp, SECRET)
    assert not verify_signature(-100123, 25, exp, sig, "other-secret")


def test_expired_signature_fails():
    exp = int(time.time()) - 10
    sig = sign_payload(-100123, 25, exp, SECRET)
    assert not verify_signature(-100123, 25, exp, sig, SECRET)


def test_empty_sig_or_exp_fails():
    exp = int(time.time()) + 60
    assert not verify_signature(-100123, 25, exp, "", SECRET)
    assert not verify_signature(-100123, 25, 0, "abc", SECRET)


def test_build_signed_query_format():
    q = build_signed_query(-100123, 25, 3600, SECRET)
    assert q.startswith("exp=")
    assert "&sig=" in q
    parts = dict(p.split("=") for p in q.split("&"))
    assert verify_signature(-100123, 25, int(parts["exp"]), parts["sig"], SECRET)


def test_build_signed_query_rejects_bad_ttl():
    with pytest.raises(ValueError):
        build_signed_query(-100123, 25, 0, SECRET)


def test_allowed_chat_validation():
    allowed = frozenset({-100123, -100456})
    assert is_chat_allowed(-100123, allowed)
    assert not is_chat_allowed(-100999, allowed)
    assert not is_chat_allowed(-100123, frozenset())


def test_parse_allowed_chat_ids():
    assert parse_allowed_chat_ids(None) == frozenset()
    assert parse_allowed_chat_ids("") == frozenset()
    assert parse_allowed_chat_ids("-100123,-100456") == frozenset({-100123, -100456})
    assert parse_allowed_chat_ids(" -100123 , ") == frozenset({-100123})


def test_parse_allowed_rejects_garbage():
    with pytest.raises(ConfigError):
        parse_allowed_chat_ids("-100123,abc")


def test_chunk_size_must_align():
    with pytest.raises(ConfigError):
        load_settings({"TELEGRAM_CHUNK_SIZE": "1000"})


def test_defaults_are_safe():
    s = load_settings({})
    assert s.allow_unsigned is False
    assert s.max_concurrent_streams >= 1
    assert s.telegram_chunk_size % 4096 == 0
    assert s.telegram_configured is False
