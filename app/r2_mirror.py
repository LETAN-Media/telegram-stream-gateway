"""Background mirror Telegram -> R2 (new account, persistent cache).

Only videos actually watched are cached. Deterministic keys, S3 multipart,
bounded memory, per-video dedup locks, size verification. Never touches
the old-account R2. Never deletes Telegram data.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger("gateway.mirror")

PART_SIZE = 8 * 1024 * 1024
MAX_RETRIES = 4


@dataclass
class R2Settings:
    enabled: bool
    endpoint: str | None
    access_key: str | None
    secret_key: str | None
    bucket: str | None
    max_file_size: int  # 0 = unlimited

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.endpoint and self.access_key and self.secret_key and self.bucket)


def _pick(env: dict[str, str], new_name: str, old_name: str) -> str | None:
    v = (env.get(new_name) or "").strip() or (env.get(old_name) or "").strip()
    return v or None


def r2_settings_from_env(env: dict[str, str]) -> R2Settings:
    raw_max = (env.get("R2_MAX_CACHE_FILE_SIZE") or "0").strip()
    try:
        max_size = max(0, int(raw_max))
    except ValueError:
        max_size = 0
    return R2Settings(
        enabled=(env.get("R2_CACHE_ENABLED") or "true").strip().lower() in ("1", "true", "yes", "on"),
        endpoint=_pick(env, "R2_NEW_ENDPOINT", "R2_ENDPOINT"),
        access_key=_pick(env, "R2_NEW_ACCESS_KEY_ID", "R2_ACCESS_KEY_ID"),
        secret_key=_pick(env, "R2_NEW_SECRET_ACCESS_KEY", "R2_SECRET_ACCESS_KEY"),
        bucket=_pick(env, "R2_NEW_BUCKET", "R2_BUCKET"),
        max_file_size=max_size,
    )


def object_key(chat_id: int, message_id: int) -> str:
    """Deterministic key. Published channel media is effectively immutable
    (edits change captions, replacements get new message ids), and the
    object metadata pins telegram_file_size for verification/reporting."""
    return f"videos/{chat_id}/{message_id}.mp4"


@dataclass
class MirrorJob:
    state: str = "CACHING"  # CACHING | READY | FAILED | SKIPPED
    error: str | None = None
    size: int = 0
    updated_at: float = field(default_factory=time.time)


class MirrorManager:
    """Per-video dedup locks + job registry. Single-instance safe."""

    def __init__(self, settings: R2Settings) -> None:
        self._settings = settings
        self._locks: dict[tuple[int, int], asyncio.Lock] = {}
        self._jobs: dict[tuple[int, int], MirrorJob] = {}
        self._client = None

    def _get_lock(self, key: tuple[int, int]) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def _s3(self):
        if self._client is not None:
            return self._client
        s = self._settings
        if not s.configured:
            return None
        from boto3 import client as boto_client

        self._client = boto_client(
            "s3",
            endpoint_url=s.endpoint,
            aws_access_key_id=s.access_key,
            aws_secret_access_key=s.secret_key,
            region_name="auto",
        )
        return self._client

    async def head_size(self, key: str) -> int | None:
        """Source of truth for READY. None = missing/error (caller falls back)."""
        s3 = self._s3()
        if s3 is None:
            return None
        try:
            resp = await asyncio.to_thread(s3.head_object, Bucket=self._settings.bucket, Key=key)
            size = resp.get("ContentLength")
            return int(size) if size is not None else None
        except Exception as e:
            log.debug("r2 head miss key=%s: %s", key, type(e).__name__)
            return None

    def job_state(self, chat_id: int, message_id: int) -> MirrorJob | None:
        return self._jobs.get((chat_id, message_id))

    def trigger(
        self,
        client: object,
        chat_id: int,
        message_id: int,
        media_getter,
    ) -> str:
        """Fire-and-forget mirror. Returns STARTED | ALREADY_RUNNING | DISABLED."""
        if not self._settings.configured:
            return "DISABLED"
        key = (chat_id, message_id)
        if key in self._jobs and self._jobs[key].state == "CACHING":
            return "ALREADY_RUNNING"
        if self._get_lock(key).locked():
            return "ALREADY_RUNNING"
        self._jobs[key] = MirrorJob()
        asyncio.create_task(self._run(client, chat_id, message_id, media_getter))
        return "STARTED"

    async def _run(self, client: object, chat_id: int, message_id: int, media_getter) -> None:
        key = (chat_id, message_id)
        job = self._jobs[key]
        lock = self._get_lock(key)
        if lock.locked():
            return
        async with lock:
            try:
                await self._mirror(client, chat_id, message_id, media_getter, job)
            except Exception as e:
                job.state = "FAILED"
                job.error = type(e).__name__
                job.updated_at = time.time()
                log.error("mirror failed chat=%s msg=%s: %s", chat_id, message_id, type(e).__name__)
            finally:
                self._locks.pop(key, None)

    async def _mirror(self, client: object, chat_id: int, message_id: int, media_getter, job: MirrorJob) -> None:
        from . import telegram_stream

        media = await media_getter(client, chat_id, message_id)
        if media is None:
            job.state = "FAILED"
            job.error = "no-media"
            return
        size = int(media.size)
        if size <= 0:
            job.state = "FAILED"
            job.error = "empty"
            return
        if self._settings.max_file_size > 0 and size > self._settings.max_file_size:
            job.state = "SKIPPED"
            job.error = "over-limit"
            return

        r2_key = object_key(chat_id, message_id)
        existing = await self.head_size(r2_key)
        if existing == size:
            job.state = "READY"
            job.size = size
            job.updated_at = time.time()
            log.info("mirror skip (already READY) key=%s", r2_key)
            return

        s3 = self._s3()
        if s3 is None:
            job.state = "FAILED"
            job.error = "r2-unconfigured"
            return
        metadata = {
            "telegram_chat_id": str(chat_id),
            "telegram_message_id": str(message_id),
            "telegram_file_size": str(size),
            "content_type": media.mime_type,
            "original_filename": media.filename[:256],
            "cached_at": str(int(time.time())),
            "storage_source": "telegram",
        }
        bucket = self._settings.bucket
        upload_id: str | None = None
        try:
            created = await asyncio.to_thread(
                s3.create_multipart_upload, Bucket=bucket, Key=r2_key,
                ContentType=media.mime_type, Metadata=metadata,
            )
            upload_id = created["UploadId"]
            part_no = 0
            buf = bytearray()
            parts = []
            uploaded = 0

            async def upload_part(data: bytes) -> None:
                nonlocal part_no, uploaded
                part_no += 1
                for attempt in range(1, MAX_RETRIES + 1):
                    try:
                        resp = await asyncio.to_thread(
                            s3.upload_part, Bucket=bucket, Key=r2_key,
                            UploadId=upload_id, PartNumber=part_no, Body=data,
                        )
                        break
                    except Exception:
                        if attempt >= MAX_RETRIES:
                            raise
                        await asyncio.sleep(min(2.0**attempt, 15.0))
                parts.append({"ETag": resp["ETag"], "PartNumber": part_no})
                uploaded += len(data)

            async for chunk in telegram_stream.iter_media_range(
                client, media, 0, size - 1,
                self._chunk_size(), self._timeout(),
            ):
                buf += chunk
                while len(buf) >= PART_SIZE:
                    head = bytes(buf[:PART_SIZE])
                    buf = buf[PART_SIZE:]
                    await upload_part(head)
            if buf:
                await upload_part(bytes(buf))
                buf = bytearray()
            if uploaded != size:
                raise RuntimeError(f"byte count mismatch {uploaded} != {size}")
            await asyncio.to_thread(
                s3.complete_multipart_upload, Bucket=bucket, Key=r2_key,
                UploadId=upload_id, MultipartUpload={"Parts": parts},
            )
            upload_id = None
            verified = await self.head_size(r2_key)
            if verified != size:
                raise RuntimeError("post-upload verification failed")
            job.state = "READY"
            job.size = size
            job.updated_at = time.time()
            log.info("mirror READY key=%s size=%s", r2_key, size)
        finally:
            if upload_id is not None:
                try:
                    await asyncio.to_thread(
                        s3.abort_multipart_upload, Bucket=bucket, Key=r2_key, UploadId=upload_id,
                    )
                except Exception:
                    pass

    def _chunk_size(self) -> int:
        return 512 * 1024

    def _timeout(self) -> float:
        return 60.0
