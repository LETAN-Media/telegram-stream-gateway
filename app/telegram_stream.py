"""MTProto access via Telethon. Streams aligned chunks, never buffers full files."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

log = logging.getLogger("gateway.telegram")

MAX_FLOOD_RETRIES = 3
METADATA_TTL_SECONDS = 30 * 60


@dataclass(frozen=True)
class MediaInfo:
    size: int
    mime_type: str
    filename: str


@dataclass(frozen=True)
class CachedMedia:
    doc_id: int
    access_hash: int
    file_reference: bytes
    size: int
    mime_type: str
    filename: str
    expires_at: float

    @property
    def fresh(self) -> bool:
        return self.expires_at > time.time()


# RAM-only metadata cache: (chat_id, message_id) -> CachedMedia. Bounded by
# video catalog size (small). Never holds body bytes.
_metadata_cache: dict[tuple[int, int], CachedMedia] = {}
_cache_lock = asyncio.Lock()


def invalidate_metadata(chat_id: int, message_id: int) -> None:
    _metadata_cache.pop((chat_id, message_id), None)


def is_file_reference_error(e: BaseException) -> bool:
    return "FileReference" in type(e).__name__ or "FILE_REFERENCE" in str(e)


def _document_filename(doc: object, fallback: str) -> str:
    attrs = getattr(doc, "attributes", None) or []
    for attr in attrs:
        fname = getattr(attr, "file_name", None)
        if fname:
            return str(fname)
    return fallback


async def get_media_info(client: object, chat_id: int, message_id: int) -> MediaInfo | None:
    """Fetch message and extract document metadata. No body download."""
    cached = await get_cached_media(client, chat_id, message_id)
    if cached is None:
        return None
    return MediaInfo(size=cached.size, mime_type=cached.mime_type, filename=cached.filename)


async def get_cached_media(client: object, chat_id: int, message_id: int) -> CachedMedia | None:
    """Single resolve per (chat, message), cached 30 min. No body download."""
    key = (chat_id, message_id)
    hit = _metadata_cache.get(key)
    if hit is not None and hit.fresh:
        return hit
    async with _cache_lock:
        hit = _metadata_cache.get(key)
        if hit is not None and hit.fresh:
            return hit
        fresh = await _resolve_media(client, chat_id, message_id)
        if fresh is None:
            return None
        _metadata_cache[key] = fresh
        return fresh


async def _resolve_media(client: object, chat_id: int, message_id: int) -> CachedMedia | None:
    try:
        entity = await client.get_entity(chat_id)
    except Exception as e:
        log.warning("get_entity failed chat=%s: %s", chat_id, type(e).__name__)
        return None
    try:
        msg = await client.get_messages(entity, ids=message_id)
    except Exception as e:
        log.warning("get_messages failed: %s", type(e).__name__)
        return None
    if msg is None:
        return None
    media = getattr(msg, "media", None)
    doc = getattr(media, "document", None) if media else None
    if doc is None:
        return None
    size = int(getattr(doc, "size", 0) or 0)
    if size <= 0:
        return None
    try:
        access_hash = int(getattr(doc, "access_hash"))
        doc_id = int(getattr(doc, "id"))
    except (TypeError, ValueError):
        return None
    mime = str(getattr(doc, "mime_type", None) or "application/octet-stream")
    return CachedMedia(
        doc_id=doc_id,
        access_hash=access_hash,
        file_reference=bytes(getattr(doc, "file_reference") or b""),
        size=size,
        mime_type=mime,
        filename=_document_filename(doc, f"{message_id}.bin"),
        expires_at=time.time() + METADATA_TTL_SECONDS,
    )


async def _get_file_bytes(
    client: object,
    location: object,
    offset: int,
    limit: int,
    timeout: float,
) -> bytes:
    try:
        from telethon.tl.functions.upload import GetFileRequest as _GetFile
    except ImportError:  # Telethon < 1.37 class name
        from telethon.tl.functions.upload import GetFile as _GetFile  # type: ignore[no-redef]

    last_error: Exception | None = None
    for attempt in range(1, MAX_FLOOD_RETRIES + 2):
        try:
            result = await asyncio.wait_for(
                client(_GetFile(location=location, offset=offset, limit=limit, precise=False)),
                timeout,
            )
            return bytes(result.bytes)
        except _flood_wait_error() as e:  # type: ignore[misc]
            wait = min(float(getattr(e, "seconds", 5)), 60.0)
            log.warning("flood wait %.0fs (attempt %d)", wait, attempt)
            if attempt > MAX_FLOOD_RETRIES:
                raise
            await asyncio.sleep(wait)
        except (asyncio.TimeoutError, ConnectionError) as e:
            last_error = e
            log.warning("chunk fetch failed attempt %d: %s", attempt, type(e).__name__)
            if attempt > MAX_FLOOD_RETRIES:
                raise
            await asyncio.sleep(min(2.0**attempt, 10.0))
    assert last_error is not None
    raise last_error


def _flood_wait_error() -> tuple[type[BaseException], ...]:
    try:
        from telethon.errors import FloodWaitError

        return (FloodWaitError,)
    except ImportError:
        return ()


def document_location(doc: object) -> object:
    from telethon.tl.types import InputDocumentFileLocation

    return InputDocumentFileLocation(
        id=int(getattr(doc, "id")),
        access_hash=int(getattr(doc, "access_hash")),
        file_reference=bytes(getattr(doc, "file_reference") or b""),
        thumb_size="",
    )


def location_for(media: object) -> object:
    from telethon.tl.types import InputDocumentFileLocation

    if isinstance(media, CachedMedia):
        return InputDocumentFileLocation(
            id=media.doc_id,
            access_hash=media.access_hash,
            file_reference=media.file_reference,
            thumb_size="",
        )
    return document_location(media)


async def iter_media_range(
    client: object,
    media: object,
    start: int,
    end: int,
    chunk_size: int,
    timeout: float,
):
    """Yield exactly bytes [start, end] of a Telegram document.

    Offsets sent to MTProto are always aligned down to chunk_size
    (a multiple of 4096); the leading surplus is trimmed so the client
    receives precisely Content-Length bytes. Bounded memory: one chunk
    in flight at a time. No disk writes.
    """
    if start < 0 or end < start or chunk_size <= 0:
        raise ValueError("invalid range parameters")
    location = location_for(media)
    aligned = (start // chunk_size) * chunk_size
    skip = start - aligned
    remaining = end - start + 1
    offset = aligned
    try:
        while remaining > 0:
            # Final fetch may be shorter than chunk_size (EOF edge); the
            # server accepts a non-multiple limit only for the last part.
            data = await _get_file_bytes(client, location, offset, chunk_size, timeout)
            if not data:
                break
            raw_len = len(data)
            if skip:
                if skip >= raw_len:
                    offset += raw_len
                    continue
                data = data[skip:]
                skip = 0
            if len(data) > remaining:
                data = data[:remaining]
            remaining -= len(data)
            offset += raw_len
            yield data
    except GeneratorExit:
        return
