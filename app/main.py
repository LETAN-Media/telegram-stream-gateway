"""telegram-stream-gateway: stream large Telegram videos via MTProto with HTTP Range.

No full-file buffering, no disk writes, no Bot API cloud getFile.
"""
from __future__ import annotations

import asyncio
import logging
import os
import hmac
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
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
from .security import check_api_key, is_chat_allowed, verify_signature

log = logging.getLogger("gateway")


def _client_holder() -> dict:
    return {"client": None, "lock": None, "connect_error": None}


async def _get_client(settings: Settings, holder: dict):
    if holder["client"] is not None and holder["client"].is_connected():
        return holder["client"]
    if holder["lock"] is None:
        holder["lock"] = asyncio.Lock()
    async with holder["lock"]:
        if holder["client"] is not None:
            client = holder["client"]
            if client.is_connected():
                return client
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    raise RuntimeError("Telegram session is not authorized")
                return client
            except Exception:
                holder["client"] = None
                try:
                    await client.disconnect()
                except Exception:
                    pass
                raise
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


def _extract_api_key(request: Request) -> str | None:
    hdr = request.headers.get("x-api-key")
    if hdr:
        return hdr.strip()
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    query_key = request.query_params.get("api_key") or request.query_params.get("token")
    if query_key:
        return query_key.strip()
    return None


def _check_auth(settings: Settings, chat_id: int, message_id: int, request: Request) -> JSONResponse | None:
    api_key = _extract_api_key(request)
    if api_key and settings.signing_secret and hmac.compare_digest(api_key, settings.signing_secret):
        return None

    if not is_chat_allowed(chat_id, settings.allowed_chat_ids, allow_all=settings.allow_all_chats):
        return _error(403, "chat not allowed")
    if settings.allow_unsigned:
        return None
    if not settings.signing_secret:
        return _error(503, "signing not configured")

    raw_exp = request.query_params.get("exp", "")
    sig = request.query_params.get("sig", "")
    if not raw_exp or not sig:
        return _error(401, "unauthorized: missing api_key or exp/sig")

    try:
        exp = int(raw_exp)
    except ValueError:
        return _error(403, "invalid exp")

    if not verify_signature(chat_id, message_id, exp, sig, settings.signing_secret):
        return _error(403, "invalid or expired signature")
    return None


def _check_chat_access(settings: Settings, chat_id: int, request: Request) -> JSONResponse | None:
    api_key = _extract_api_key(request)
    if api_key and settings.signing_secret and hmac.compare_digest(api_key, settings.signing_secret):
        return None
    if not is_chat_allowed(chat_id, settings.allowed_chat_ids, allow_all=settings.allow_all_chats):
        return _error(403, "chat not allowed")
    return None


def _base_headers(info: object, size: int, as_attachment: bool = False) -> dict[str, str]:
    fname = getattr(info, "filename", "video.bin")
    disposition = "attachment" if as_attachment else "inline"
    return {
        "Accept-Ranges": "bytes",
        "Content-Type": getattr(info, "mime_type", "application/octet-stream"),
        "Content-Disposition": f'{disposition}; filename="{fname}"',
        "ETag": f'"{abs(hash((getattr(info, "filename", ""), size))) % 10**12}-{size}"',
        "Cache-Control": "private, max-age=3600",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Expose-Headers": "Content-Range, Content-Length, Accept-Ranges, ETag",
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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Range", "Accept-Ranges", "Content-Length", "ETag"],
    )

    from .r2_mirror import MirrorManager, object_key, r2_settings_from_env

    mirror = MirrorManager(r2_settings_from_env(dict(os.environ)))

    @app.get("/healthz")
    async def healthz():
        connected = holder["client"] is not None and holder["client"].is_connected()
        return {
            "ok": True,
            "version": "2.0.0",
            "service": "telegram-stream-gateway",
            "telegram_connected": connected,
            "telegram_configured": cfg.telegram_configured,
            "allow_all_chats": cfg.allow_all_chats,
            "allowed_chat_count": len(cfg.allowed_chat_ids),
        }

    @app.get("/v1/chats")
    async def list_chats(request: Request):
        api_key = _extract_api_key(request)
        if not (cfg.allow_unsigned or (api_key and cfg.signing_secret and hmac.compare_digest(api_key, cfg.signing_secret))):
            return _error(401, "unauthorized: api_key required to list chats")
        if not cfg.telegram_configured:
            return _error(503, "telegram not configured")
        try:
            client = await _get_client(cfg, holder)
            dialogs = await client.get_dialogs(limit=100)
            chats = []
            for d in dialogs:
                entity = d.entity
                chats.append({
                    "id": d.id,
                    "title": d.title or getattr(entity, "first_name", "") or str(d.id),
                    "username": getattr(entity, "username", None),
                    "type": "channel" if getattr(entity, "broadcast", False) else ("megagroup" if getattr(entity, "megagroup", False) else "chat"),
                    "unread_count": d.unread_count,
                })
            return JSONResponse({"ok": True, "count": len(chats), "chats": chats})
        except Exception as e:
            log.error("list_chats failed: %s", type(e).__name__)
            return _error(502, f"telegram error: {e}")

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
        try:
            media = await telegram_stream.get_cached_media(client, chat_id, message_id)
        except (ConnectionError, OSError):
            log.warning("Telegram connection unavailable while resolving media")
            return _error(503, "telegram unavailable")
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
        try:
            media = await telegram_stream.get_cached_media(client, chat_id, message_id)
        except (ConnectionError, OSError):
            log.warning("Telegram connection unavailable while resolving media")
            return None, _error(503, "telegram unavailable")
        if media is None:
            return None, _error(404, "video not found")
        return (client, media), None

    @app.get("/v1/info/{chat_id}/{message_id}")
    async def get_info(chat_id: int, message_id: int, request: Request):
        denied = _check_auth(cfg, chat_id, message_id, request)
        if denied is not None:
            return denied
        resolved, err = await _media_or_error(chat_id, message_id)
        if err is not None:
            return err
        _, media = resolved
        return JSONResponse({
            "ok": True,
            "chat_id": chat_id,
            "message_id": message_id,
            "filename": media.filename,
            "size": media.size,
            "mime_type": media.mime_type,
            "caption": getattr(media, "caption", ""),
            "date": getattr(media, "date", 0),
            "stream_url": f"/v1/stream/{chat_id}/{message_id}",
            "file_url": f"/v1/file/{chat_id}/{message_id}",
            "thumb_url": f"/v1/thumb/{chat_id}/{message_id}",
        })

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

    @app.head("/v1/stream/{chat_id}/{message_id}")
    async def stream_head(chat_id: int, message_id: int, request: Request):
        return await video_head(chat_id, message_id, request)

    @app.get("/v1/stream/{chat_id}/{message_id}")
    async def stream_get(chat_id: int, message_id: int, request: Request):
        return await video_get(chat_id, message_id, request)

    @app.head("/v1/file/{chat_id}/{message_id}")
    async def file_head(chat_id: int, message_id: int, request: Request):
        denied = _check_auth(cfg, chat_id, message_id, request)
        if denied is not None:
            return denied
        resolved, err = await _media_or_error(chat_id, message_id)
        if err is not None:
            return err
        _, media = resolved
        headers = _base_headers(media, media.size, as_attachment=True)
        headers["Content-Length"] = str(media.size)
        return JSONResponse(None, status_code=200, headers=headers)

    @app.get("/v1/file/{chat_id}/{message_id}")
    async def file_get(chat_id: int, message_id: int, request: Request):
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
            return await _stream_response(request, chat_id, message_id, cfg, holder, as_attachment=True)
        finally:
            semaphore.release()

    async def _stream_response(
        request: Request,
        chat_id: int,
        message_id: int,
        cfg: Settings,
        holder: dict,
        as_attachment: bool = False,
    ):
        resolved, err = await _media_or_error(chat_id, message_id)
        if err is not None:
            return err
        client, media = resolved
        size = media.size
        base = _base_headers(media, size, as_attachment=as_attachment)

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

    _feed_cache: dict[tuple[int, int, int, str], tuple[float, dict]] = {}
    _poster_cache: dict[tuple[int, int], int] = {}
    _thumb_cache: dict[tuple[int, int], tuple[float, bytes]] = {}

    @app.get("/v1/feed/{chat_id}")
    async def get_feed(
        chat_id: int,
        request: Request,
        limit: int = 100,
        offset_id: int = 0,
        type: str = "video",
    ):
        denied = _check_chat_access(cfg, chat_id, request)
        if denied is not None:
            return denied
        limit = max(1, min(limit, 200))
        media_filter = type.lower().strip()
        cache_key = (chat_id, limit, offset_id, media_filter)
        now = time.time()
        cached = _feed_cache.get(cache_key)
        if cached and cached[0] > now:
            return JSONResponse(cached[1])
        try:
            client = await _get_client(cfg, holder)
            entity = await client.get_entity(chat_id)
            kwargs = {"limit": limit}
            if offset_id > 0:
                kwargs["offset_id"] = offset_id
            msgs = await client.get_messages(entity, **kwargs)
        except Exception as e:
            log.error("feed fetch failed: %s", type(e).__name__)
            return _error(502, "telegram unavailable")

        pending_photos = {}
        filtered_msgs = []
        for m in msgs:
            is_photo = bool(getattr(m, "photo", None))
            doc = getattr(m, "document", None)
            mime = getattr(doc, "mime_type", "") if doc else ""
            is_video = bool(getattr(m, "video", None)) or (doc is not None and "video" in mime)
            is_audio = bool(getattr(m, "audio", None)) or (doc is not None and "audio" in mime)
            is_doc = bool(doc is not None)

            if is_photo:
                if getattr(m, "grouped_id", None):
                    pending_photos[m.grouped_id] = m.id
                pending_photos[int(m.date.timestamp())] = m.id

            match = False
            if media_filter == "video" and is_video:
                match = True
            elif media_filter == "audio" and is_audio:
                match = True
            elif media_filter == "photo" and is_photo:
                match = True
            elif media_filter in ("all", "any", "file", "*") and (is_video or is_audio or is_doc or is_photo):
                match = True

            if match:
                filtered_msgs.append(m)

        items = []
        for m in filtered_msgs:
            cap = (m.message or "").strip()
            lines = cap.split("\n")
            title = lines[0].strip() if lines and lines[0].strip() else f"Media {m.date.strftime('%d/%m/%Y')}"
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

            if poster_mid:
                _poster_cache[(chat_id, m.id)] = poster_mid

            doc = getattr(m, "document", None)
            mime = getattr(doc, "mime_type", "") if doc else ("image/jpeg" if getattr(m, "photo", None) else "")
            size = getattr(doc, "size", 0) if doc else 0
            fname = telegram_stream._document_filename(doc, f"{m.id}.bin") if doc else None
            m_type = "video" if (getattr(m, "video", None) or "video" in mime) else (
                "audio" if (getattr(m, "audio", None) or "audio" in mime) else ("photo" if getattr(m, "photo", None) else "file")
            )

            items.append({
                "mid": m.id,
                "title": title,
                "desc": desc,
                "date": int(m.date.timestamp()),
                "duration": getattr(m.video, "duration", 0) if getattr(m, "video", None) else (
                    getattr(m.audio, "duration", 0) if getattr(m, "audio", None) else 0
                ),
                "type": m_type,
                "size": size,
                "mime_type": mime,
                "filename": fname,
                "stream": f"/api/play?mid={m.id}",
                "stream_url": f"/v1/stream/{chat_id}/{m.id}",
                "file_url": f"/v1/file/{chat_id}/{m.id}",
                "thumb": f"/api/thumb?mid={m.id}" + ("&v=p" if poster_mid else ""),
                "thumb_url": f"/v1/thumb/{chat_id}/{m.id}",
                "has_poster": bool(poster_mid),
                "poster_mid": poster_mid,
            })

        feed_data = {"ok": True, "configured": True, "count": len(items), "items": items}
        _feed_cache[cache_key] = (now + 60.0, feed_data)
        return JSONResponse(feed_data)

    @app.get("/v1/thumb/{chat_id}/{message_id}")
    async def get_thumb(chat_id: int, message_id: int, request: Request):
        denied = _check_chat_access(cfg, chat_id, request)
        if denied is not None:
            return denied
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
            target_id = _poster_cache.get((chat_id, message_id), message_id)
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
            if len(_thumb_cache) > 200:
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
        allow_all_chats=False,
    )
)
