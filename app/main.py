"""telegram-stream-gateway: stream large Telegram videos via MTProto with HTTP Range.

No full-file buffering, no disk writes, no Bot API cloud getFile.
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import telegram_stream
from .config import ConfigError, Settings, load_settings
from .range_parser import (
    ByteRange,
    RangeUnsatisfiable,
    content_range_value,
    parse_range,
    unsatisfiable_value,
)
from .security import is_chat_allowed, verify_signature

log = logging.getLogger("gateway")


def _client_holder() -> dict:
    return {"client": None, "lock": None, "connect_error": None}


async def _get_client(settings: Settings, holder: dict):
    if holder["client"] is not None:
        return holder["client"]
    if holder["lock"] is None:
        holder["lock"] = asyncio.Lock()
    async with holder["lock"]:
        if holder["client"] is not None:
            return holder["client"]
        client = await _connect(settings, holder)
        holder["client"] = client
        return client


async def _connect(settings: Settings, holder: dict):
    if not settings.telegram_configured:
        raise RuntimeError("telegram not configured")
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    assert settings.api_id is not None and settings.api_hash
    if settings.session_string:
        client = TelegramClient(StringSession(settings.session_string), settings.api_id, settings.api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError("session string is not authorized")
        return client
    assert settings.bot_token
    client = TelegramClient("bot", settings.api_id, settings.api_hash)
    await client.start(bot_token=settings.bot_token)
    return client


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status)


def _check_auth(settings: Settings, chat_id: int, message_id: int, request: Request) -> JSONResponse | None:
    if not is_chat_allowed(chat_id, settings.allowed_chat_ids):
        return _error(403, "chat not allowed")
    if settings.allow_unsigned:
        return None
    if not settings.signing_secret:
        return _error(503, "signing not configured")
    try:
        exp = int(request.query_params.get("exp", ""))
    except ValueError:
        return _error(403, "invalid exp")
    sig = request.query_params.get("sig", "")
    if not verify_signature(chat_id, message_id, exp, sig, settings.signing_secret):
        return _error(403, "invalid or expired signature")
    return None


def _base_headers(info: object, size: int) -> dict[str, str]:
    return {
        "Accept-Ranges": "bytes",
        "Content-Type": getattr(info, "mime_type", "application/octet-stream"),
        "Content-Disposition": f'inline; filename="{getattr(info, "filename", "video.bin")}"',
        "ETag": f'"{abs(hash((getattr(info, "filename", ""), size))) % 10**12}-{size}"',
        "Cache-Control": "private, max-age=3600",
    }


async def _resolve_document(client: object, chat_id: int, message_id: int):
    try:
        entity = await client.get_entity(chat_id)
    except Exception:
        return None
    try:
        msg = await client.get_messages(entity, ids=message_id)
    except Exception:
        return None
    if msg is None:
        return None
    media = getattr(msg, "media", None)
    doc = getattr(media, "document", None) if media else None
    if doc is None or int(getattr(doc, "size", 0) or 0) <= 0:
        return None
    return doc


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or load_settings()
    logging.basicConfig(level=cfg.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    holder = _client_holder()
    semaphore = asyncio.Semaphore(cfg.max_concurrent_streams)

    if not cfg.allowed_chat_ids:
        log.warning("ALLOWED_CHAT_IDS is empty: all video requests will be denied")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        client = holder["client"]
        if client is not None:
            try:
                await client.disconnect()
            except Exception as e:
                log.warning("disconnect failed: %s", type(e).__name__)

    app = FastAPI(title="telegram-stream-gateway", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz():
        connected = holder["client"] is not None
        return {
            "ok": True,
            "telegram_connected": connected,
            "telegram_configured": cfg.telegram_configured,
        }

    async def _media_or_error(chat_id: int, message_id: int):
        if not cfg.telegram_configured:
            return None, _error(503, "telegram not configured")
        try:
            client = await _get_client(cfg, holder)
        except Exception as e:
            log.error("telegram connect failed: %s", type(e).__name__)
            return None, _error(502, "telegram unavailable")
        doc = await _resolve_document(client, chat_id, message_id)
        if doc is None:
            return None, _error(404, "video not found")
        return (client, doc), None

    @app.head("/v1/video/{chat_id}/{message_id}")
    async def video_head(chat_id: int, message_id: int, request: Request):
        denied = _check_auth(cfg, chat_id, message_id, request)
        if denied is not None:
            return denied
        resolved, err = await _media_or_error(chat_id, message_id)
        if err is not None:
            return err
        _, doc = resolved
        size = int(doc.size)
        info = await telegram_stream.get_media_info(holder["client"], chat_id, message_id)
        headers = _base_headers(info or doc, size)
        headers["Content-Length"] = str(size)
        return JSONResponse(None, status_code=200, headers=headers)

    @app.get("/v1/video/{chat_id}/{message_id}")
    async def video_get(chat_id: int, message_id: int, request: Request):
        denied = _check_auth(cfg, chat_id, message_id, request)
        if denied is not None:
            return denied
        try:
            acquired = await asyncio.wait_for(semaphore.acquire(), timeout=10.0)
        except asyncio.TimeoutError:
            return _error(503, "server busy")
        if not acquired:
            return _error(503, "server busy")
        try:
            return await _stream_response(request, chat_id, message_id, cfg, holder)
        finally:
            semaphore.release()

    async def _stream_response(request: Request, chat_id: int, message_id: int, cfg: Settings, holder: dict):
        resolved, err = await _media_or_error(chat_id, message_id)
        if err is not None:
            return err
        client, doc = resolved
        size = int(doc.size)
        info = await telegram_stream.get_media_info(client, chat_id, message_id)
        base = _base_headers(info or doc, size)

        raw_range = request.headers.get("range")
        try:
            wanted: ByteRange | None = parse_range(raw_range, size)
        except RangeUnsatisfiable:
            return JSONResponse(
                {"ok": False, "error": "range not satisfiable"},
                status_code=416,
                headers={"Content-Range": unsatisfiable_value(size), "Accept-Ranges": "bytes"},
            )
        if wanted is None:
            wanted = ByteRange(0, size - 1)
            status = 200
        else:
            status = 206

        async def body():
            try:
                async for chunk in telegram_stream.iter_media_range(
                    client, doc, wanted.start, wanted.end, cfg.telegram_chunk_size, cfg.request_timeout_seconds
                ):
                    yield chunk
            except GeneratorExit:
                return
            except Exception as e:
                log.error("stream failed: %s", type(e).__name__)
                return

        headers = dict(base)
        headers["Content-Length"] = str(wanted.length)
        if status == 206:
            headers["Content-Range"] = content_range_value(wanted, size)
        return StreamingResponse(body(), status_code=status, headers=headers)

    return app


try:
    _settings = load_settings()
except ConfigError as _e:
    logging.getLogger("gateway").warning("config incomplete at import: %s", _e)
    _settings = None

app = create_app(_settings) if _settings is not None else create_app(
    Settings(
        api_id=None, api_hash=None, bot_token=None, session_string=None,
        allowed_chat_ids=frozenset(), signing_secret=None, allow_unsigned=False,
        max_concurrent_streams=4, telegram_chunk_size=512 * 1024,
        request_timeout_seconds=30.0, port=8080, log_level="info",
    )
)
