"""telegram-stream-gateway: stream large Telegram videos via MTProto with HTTP Range.

No full-file buffering, no disk writes, no Bot API cloud getFile.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

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


async def _startup_warm(cfg: Settings, holder: dict) -> None:
    """Connect MTProto at startup and warm the DC connection.

    Never fails startup: on error the client stays lazy (first request
    connects). Keeps Northflank healthchecks green while Telegram is down.
    """
    if not cfg.telegram_configured:
        log.warning("telegram not configured; video endpoints will return 503")
        return
    try:
        client = await _get_client(cfg, holder)
    except Exception as e:
        log.warning("startup connect failed (lazy retry on request): %s", type(e).__name__)
        return
    try:
        me = await client.get_me()
        log.info("telegram connected, warmed DC (as=%s)", getattr(me, "username", "?"))
    except Exception as e:
        log.warning("telegram warm failed (lazy retry on request): %s", type(e).__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or load_settings()
    logging.basicConfig(level=cfg.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    holder = _client_holder()
    semaphore = asyncio.Semaphore(cfg.max_concurrent_streams)

    if not cfg.allowed_chat_ids:
        log.warning("ALLOWED_CHAT_IDS is empty: all video requests will be denied")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await _startup_warm(cfg, holder)
        yield
        client = holder["client"]
        if client is not None:
            try:
                await client.disconnect()
            except Exception as e:
                log.warning("disconnect failed: %s", type(e).__name__)

    app = FastAPI(title="telegram-stream-gateway", lifespan=lifespan)

    from .r2_mirror import MirrorManager, object_key, r2_settings_from_env

    mirror = MirrorManager(r2_settings_from_env(dict(os.environ)))

    @app.get("/healthz")
    async def healthz():
        connected = holder["client"] is not None
        return {
            "ok": True,
            "telegram_connected": connected,
            "telegram_configured": cfg.telegram_configured,
        }

    @app.post("/v1/cache/{chat_id}/{message_id}")
    async def cache_trigger(chat_id: int, message_id: int, request: Request):
        denied = _check_auth(cfg, chat_id, message_id, request)
        if denied is not None:
            return denied
        if not cfg.telegram_configured:
            return _error(503, "telegram not configured")
        try:
            client = await _get_client(cfg, holder)
        except Exception as e:
            log.error("telegram connect failed: %s", type(e).__name__)
            return _error(502, "telegram unavailable")
        state = mirror.trigger(client, chat_id, message_id, telegram_stream.get_cached_media)
        return JSONResponse({"ok": True, "cache": state})

    @app.get("/v1/cache-status/{chat_id}/{message_id}")
    async def cache_status(chat_id: int, message_id: int, request: Request):
        denied = _check_auth(cfg, chat_id, message_id, request)
        if denied is not None:
            return denied
        if not cfg.telegram_configured:
            return _error(503, "telegram not configured")
        try:
            client = await _get_client(cfg, holder)
        except Exception:
            return _error(502, "telegram unavailable")
        media = await telegram_stream.get_cached_media(client, chat_id, message_id)
        if media is None:
            return _error(404, "video not found")
        key = object_key(chat_id, message_id)
        found = await mirror.head_size(key)
        if found == int(media.size):
            return JSONResponse({"ok": True, "state": "READY", "size": found, "key": key})
        job = mirror.job_state(chat_id, message_id)
        if job is not None and job.state in ("CACHING", "FAILED", "SKIPPED"):
            return JSONResponse({"ok": True, "state": job.state, "size": int(media.size),
                                 "error": job.error, "key": key})
        return JSONResponse({"ok": True, "state": "MISS", "size": int(media.size), "key": key})

    async def _media_or_error(chat_id: int, message_id: int):
        if not cfg.telegram_configured:
            return None, _error(503, "telegram not configured")
        try:
            client = await _get_client(cfg, holder)
        except Exception as e:
            log.error("telegram connect failed: %s", type(e).__name__)
            return None, _error(502, "telegram unavailable")
        media = await telegram_stream.get_cached_media(client, chat_id, message_id)
        if media is None:
            return None, _error(404, "video not found")
        return (client, media), None

    @app.head("/v1/video/{chat_id}/{message_id}")
    async def video_head(chat_id: int, message_id: int, request: Request):
        denied = _check_auth(cfg, chat_id, message_id, request)
        if denied is not None:
            return denied
        resolved, err = await _media_or_error(chat_id, message_id)
        if err is not None:
            return err
        _, media = resolved
        headers = _base_headers(media, media.size)
        headers["Content-Length"] = str(media.size)
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
        client, media = resolved
        size = media.size
        base = _base_headers(media, size)

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
                    client, media, wanted.start, wanted.end, cfg.telegram_chunk_size, cfg.request_timeout_seconds
                ):
                    yield chunk
                return
            except GeneratorExit:
                return
            except Exception as e:
                if not telegram_stream.is_file_reference_error(e):
                    log.error("stream failed: %s", type(e).__name__)
                    return
            log.warning("file reference expired; re-resolving once")
            telegram_stream.invalidate_metadata(chat_id, message_id)
            fresh = await telegram_stream.get_cached_media(client, chat_id, message_id)
            if fresh is None:
                return
            try:
                async for chunk in telegram_stream.iter_media_range(
                    client, fresh, wanted.start, wanted.end, cfg.telegram_chunk_size, cfg.request_timeout_seconds
                ):
                    yield chunk
            except GeneratorExit:
                return
            except Exception as e:
                log.error("stream retry failed: %s", type(e).__name__)
                return

        headers = dict(base)
        headers["Content-Length"] = str(wanted.length)
        if status == 206:
            headers["Content-Range"] = content_range_value(wanted, size)
        return StreamingResponse(body(), status_code=status, headers=headers)

    _feed_cache: dict[int, tuple[float, dict]] = {}
    _thumb_cache: dict[tuple[int, int], tuple[float, bytes]] = {}

    @app.get("/v1/feed/{chat_id}")
    async def get_feed(chat_id: int):
        if not is_chat_allowed(chat_id, cfg.allowed_chat_ids):
            return _error(403, "chat not allowed")
        now = time.time()
        cached = _feed_cache.get(chat_id)
        if cached and cached[0] > now:
            return JSONResponse(cached[1])
        try:
            client = await _get_client(cfg, holder)
            entity = await client.get_entity(chat_id)
            msgs = await client.get_messages(entity, limit=100)
        except Exception as e:
            log.error("feed fetch failed: %s", type(e).__name__)
            return _error(502, "telegram unavailable")

        pending_photos = {}
        videos = []
        for m in msgs:
            if getattr(m, "photo", None):
                if getattr(m, "grouped_id", None):
                    pending_photos[m.grouped_id] = m.id
                pending_photos[int(m.date.timestamp())] = m.id
            elif getattr(m, "video", None) or (getattr(m, "document", None) and "video" in getattr(m.document, "mime_type", "")):
                videos.append(m)

        items = []
        for m in videos:
            cap = (m.message or "").strip()
            lines = cap.split("\n")
            title = lines[0].strip() if lines and lines[0].strip() else f"Video {m.date.strftime('%d/%m/%Y')}"
            desc = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""

            poster_mid = None
            if getattr(m, "grouped_id", None) and m.grouped_id in pending_photos:
                poster_mid = pending_photos[m.grouped_id]
            else:
                ts = int(m.date.timestamp())
                for pts, pid in pending_photos.items():
                    if abs(pts - ts) <= 900:
                        poster_mid = pid
                        break

            items.append({
                "mid": m.id,
                "title": title,
                "desc": desc,
                "date": int(m.date.timestamp()),
                "duration": getattr(m.video, "duration", 0) if getattr(m, "video", None) else 0,
                "stream": f"/api/play?mid={m.id}",
                "thumb": f"/api/thumb?mid={m.id}" + ("&v=p" if poster_mid else ""),
                "has_poster": bool(poster_mid),
                "poster_mid": poster_mid,
            })

        feed_data = {"ok": True, "configured": True, "count": len(items), "items": items}
        _feed_cache[chat_id] = (now + 60.0, feed_data)
        return JSONResponse(feed_data)

    @app.get("/v1/thumb/{chat_id}/{message_id}")
    async def get_thumb(chat_id: int, message_id: int):
        if not is_chat_allowed(chat_id, cfg.allowed_chat_ids):
            return _error(403, "chat not allowed")
        key = (chat_id, message_id)
        now = time.time()
        cached = _thumb_cache.get(key)
        if cached and cached[0] > now:
            return Response(
                content=cached[1],
                media_type="image/jpeg",
                headers={"Cache-Control": "public, max-age=604800, s-maxage=604800, immutable"},
            )
        try:
            client = await _get_client(cfg, holder)
            entity = await client.get_entity(chat_id)
            target_id = message_id
            feed_cached = _feed_cache.get(chat_id)
            if feed_cached:
                for it in feed_cached[1].get("items", []):
                    if it.get("mid") == message_id and it.get("poster_mid"):
                        target_id = it["poster_mid"]
                        break
            msg = await client.get_messages(entity, ids=target_id)
            if not msg:
                return _error(404, "not found")
            img_bytes = await client.download_media(msg, thumb=-1 if not getattr(msg, "photo", None) else None, file=bytes)
            if not img_bytes and target_id != message_id:
                orig_msg = await client.get_messages(entity, ids=message_id)
                if orig_msg:
                    img_bytes = await client.download_media(orig_msg, thumb=-1, file=bytes)
            if not img_bytes:
                return _error(404, "no thumbnail")
            if len(_thumb_cache) > 100:
                _thumb_cache.clear()
            _thumb_cache[key] = (now + 86400.0, bytes(img_bytes))
            return Response(
                content=bytes(img_bytes),
                media_type="image/jpeg",
                headers={"Cache-Control": "public, max-age=604800, s-maxage=604800, immutable"},
            )
        except Exception as e:
            log.error("thumb fetch failed: %s", type(e).__name__)
            return _error(502, "telegram unavailable")

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
