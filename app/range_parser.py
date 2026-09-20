"""HTTP Range parsing and Content-Range formatting. Pure functions, fully tested."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ByteRange:
    start: int
    end: int  # inclusive

    @property
    def length(self) -> int:
        return self.end - self.start + 1


def parse_range(header: str | None, size: int) -> ByteRange | None:
    """Parse a single Range header against a known size.

    Returns None when the header is absent (caller serves full body).
    Raises RangeUnsatisfiable when the header is present but invalid.
    """
    if size <= 0:
        raise RangeUnsatisfiable(size)
    if header is None or not header.strip():
        return None
    text = header.strip()
    if not text.lower().startswith("bytes="):
        raise RangeUnsatisfiable(size)
    spec = text[6:].strip()
    if "," in spec:
        raise RangeUnsatisfiable(size)  # multi-range not supported
    if "-" not in spec:
        raise RangeUnsatisfiable(size)
    first, _, last = spec.partition("-")
    first = first.strip()
    last = last.strip()

    if first == "" and last == "":
        raise RangeUnsatisfiable(size)

    if first == "":
        try:
            suffix = int(last)
        except ValueError:
            raise RangeUnsatisfiable(size)
        if suffix <= 0:
            raise RangeUnsatisfiable(size)
        start = max(0, size - suffix)
        return ByteRange(start, size - 1)

    try:
        start = int(first)
    except ValueError:
        raise RangeUnsatisfiable(size)
    if start < 0 or start >= size:
        raise RangeUnsatisfiable(size)

    if last == "":
        return ByteRange(start, size - 1)
    try:
        end = int(last)
    except ValueError:
        raise RangeUnsatisfiable(size)
    if end < start:
        raise RangeUnsatisfiable(size)
    return ByteRange(start, min(end, size - 1))


class RangeUnsatisfiable(Exception):
    def __init__(self, size: int) -> None:
        super().__init__(f"unsatisfiable range for size {size}")
        self.size = size


def content_range_value(r: ByteRange, size: int) -> str:
    return f"bytes {r.start}-{r.end}/{size}"


def unsatisfiable_value(size: int) -> str:
    return f"bytes */{size}"
