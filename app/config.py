"""Typed configuration loaded from ENV only. No defaults for secrets."""
from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    pass


def _get(env: dict[str, str], name: str) -> str | None:
    v = env.get(name)
    if v is None:
        return None
    v = v.strip()
    return v if v else None


def _get_int(env: dict[str, str], name: str, default: int, minimum: int) -> int:
    raw = _get(env, name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer")
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}")
    return value


def _get_float(env: dict[str, str], name: str, default: float, minimum: float) -> float:
    raw = _get(env, name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number")
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}")
    return value


def _get_bool(env: dict[str, str], name: str, default: bool) -> bool:
    raw = _get(env, name)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    api_id: int | None
    api_hash: str | None
    bot_token: str | None
    session_string: str | None
    allowed_chat_ids: frozenset[int]
    signing_secret: str | None
    allow_unsigned: bool
    max_concurrent_streams: int
    telegram_chunk_size: int
    request_timeout_seconds: float
    port: int
    log_level: str
    allow_all_chats: bool = False

    @property
    def telegram_configured(self) -> bool:
        if self.api_id is None or not self.api_hash:
            return False
        return bool(self.session_string or self.bot_token)


def parse_allowed_chat_ids(raw: str | None) -> frozenset[int]:
    if not raw:
        return frozenset()
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part or part in ("*", "all", "ALL"):
            continue
        try:
            ids.add(int(part))
        except ValueError:
            raise ConfigError(f"ALLOWED_CHAT_IDS contains non-integer: {part!r}")
    return frozenset(ids)


def load_settings(env: dict[str, str] | None = None) -> Settings:
    src = dict(os.environ) if env is None else dict(env)

    api_id: int | None = None
    raw_api_id = _get(src, "TELEGRAM_API_ID")
    if raw_api_id is not None:
        try:
            api_id = int(raw_api_id)
        except ValueError:
            raise ConfigError("TELEGRAM_API_ID must be an integer")
        if api_id <= 0:
            raise ConfigError("TELEGRAM_API_ID must be > 0")

    chunk = _get_int(src, "TELEGRAM_CHUNK_SIZE", 512 * 1024, 4096)
    if chunk % 4096 != 0:
        raise ConfigError("TELEGRAM_CHUNK_SIZE must be a multiple of 4096")

    raw_allowed = _get(src, "ALLOWED_CHAT_IDS")
    allow_all = _get_bool(src, "ALLOW_ALL_CHATS", False)
    if raw_allowed:
        parts = [p.strip().lower() for p in raw_allowed.split(",") if p.strip()]
        if "*" in parts or "all" in parts:
            allow_all = True

    secret = _get(src, "STREAM_SIGNING_SECRET") or _get(src, "GATEWAY_API_KEY") or _get(src, "API_KEY")

    return Settings(
        api_id=api_id,
        api_hash=_get(src, "TELEGRAM_API_HASH"),
        bot_token=_get(src, "TELEGRAM_BOT_TOKEN"),
        session_string=_get(src, "TELEGRAM_SESSION_STRING"),
        allowed_chat_ids=parse_allowed_chat_ids(raw_allowed),
        signing_secret=secret,
        allow_unsigned=_get_bool(src, "ALLOW_UNSIGNED_STREAMS", False),
        max_concurrent_streams=_get_int(src, "MAX_CONCURRENT_STREAMS", 4, 1),
        telegram_chunk_size=chunk,
        request_timeout_seconds=_get_float(src, "REQUEST_TIMEOUT_SECONDS", 30.0, 1.0),
        port=_get_int(src, "PORT", 8080, 1),
        log_level=(_get(src, "LOG_LEVEL") or "info").lower(),
        allow_all_chats=allow_all,
    )
