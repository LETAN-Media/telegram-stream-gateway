"""Unit tests: Range parser, Content-Range, invalid Range."""
from app.range_parser import (
    ByteRange,
    RangeUnsatisfiable,
    content_range_value,
    parse_range,
    unsatisfiable_value,
)


def test_no_header_returns_none():
    assert parse_range(None, 100) is None
    assert parse_range("", 100) is None
    assert parse_range("   ", 100) is None


def test_full_prefix_open_end():
    assert parse_range("bytes=0-", 100) == ByteRange(0, 99)


def test_closed_range():
    assert parse_range("bytes=0-1048575", 2000000) == ByteRange(0, 1048575)


def test_closed_range_clamped_to_size():
    assert parse_range("bytes=0-9999999", 100) == ByteRange(0, 99)


def test_suffix_range():
    assert parse_range("bytes=-1024", 10000) == ByteRange(8976, 9999)


def test_suffix_larger_than_file():
    assert parse_range("bytes=-99999", 100) == ByteRange(0, 99)


def test_middle_range():
    assert parse_range("bytes=1000000-1999999", 5000000) == ByteRange(1000000, 1999999)


def test_length_property():
    assert ByteRange(0, 1048575).length == 1048576


def test_content_range_format():
    assert content_range_value(ByteRange(0, 1048575), 2000000) == "bytes 0-1048575/2000000"


def test_unsatisfiable_value_format():
    assert unsatisfiable_value(2000000) == "bytes */2000000"


def test_start_beyond_size_is_416():
    for bad in ("bytes=100-", "bytes=200-300"):
        try:
            parse_range(bad, 100)
        except RangeUnsatisfiable as e:
            assert e.size == 100
        else:
            raise AssertionError(bad)


def test_malformed_headers_are_416():
    for bad in ("bytes=-", "bytes=abc-def", "bytes=5-2", "bytes=0-1,2-3", "items=0-10", "bytes", "bytes=-0"):
        try:
            parse_range(bad, 1000)
        except RangeUnsatisfiable:
            continue
        raise AssertionError(bad)


def test_zero_size_is_416():
    try:
        parse_range("bytes=0-", 0)
    except RangeUnsatisfiable:
        return
    raise AssertionError("expected RangeUnsatisfiable")
