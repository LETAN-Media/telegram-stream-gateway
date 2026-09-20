"""Unit tests: aligned MTProto chunk streaming trims exactly the requested bytes."""
import asyncio

import app.telegram_stream as ts
from app.telegram_stream import iter_media_range


class FakeDoc:
    id = 1
    access_hash = 2
    file_reference = b"ref"
    size = 10 * 1024


BLOB = bytes(range(256)) * 40  # 10240 bytes


def _collect(start, end, chunk=512):
    async def go():
        out = b""
        async for data in iter_media_range(None, FakeDoc(), start, end, chunk, 5.0):
            out += data
        return out

    return asyncio.new_event_loop().run_until_complete(go())


def test_aligned_start_streams_exact_bytes(monkeypatch):
    async def fake_get(client, location, offset, limit, timeout):
        assert offset % 512 == 0, "offset must be aligned"
        return BLOB[offset:offset + limit]

    monkeypatch.setattr(ts, "_get_file_bytes", fake_get)
    assert _collect(0, 1023) == BLOB[0:1024]


def test_unaligned_start_trims_leading_surplus(monkeypatch):
    async def fake_get(client, location, offset, limit, timeout):
        assert offset % 512 == 0, "offset must be aligned"
        return BLOB[offset:offset + limit]

    monkeypatch.setattr(ts, "_get_file_bytes", fake_get)
    out = _collect(100, 1123)
    assert out == BLOB[100:1124]
    assert len(out) == 1024


def test_range_never_returns_more_than_content_length(monkeypatch):
    async def fake_get(client, location, offset, limit, timeout):
        return BLOB[offset:offset + limit]

    monkeypatch.setattr(ts, "_get_file_bytes", fake_get)
    assert _collect(0, 99) == BLOB[0:100]
