"""Unit tests for the Python SDK."""
import time
from unittest.mock import MagicMock, patch
from sdk.telegram_gateway import TelegramGateway, GatewayError
from app.security import verify_signature

SECRET = "my-secret-key-12345"
GATEWAY_URL = "https://gateway.example.com"


def test_sdk_stream_url_generation():
    gw = TelegramGateway(GATEWAY_URL, secret=SECRET)
    url = gw.get_stream_url(-100123, 42, ttl_seconds=3600)

    assert url.startswith("https://gateway.example.com/v1/stream/-100123/42?exp=")
    assert "&sig=" in url

    # Parse query
    query = url.split("?")[1]
    params = dict(p.split("=") for p in query.split("&"))
    exp = int(params["exp"])
    sig = params["sig"]

    assert exp > int(time.time())
    assert verify_signature(-100123, 42, exp, sig, SECRET)


def test_sdk_download_url_generation():
    gw = TelegramGateway(GATEWAY_URL, secret=SECRET)
    url = gw.get_download_url(-100123, 42, ttl_seconds=3600)

    assert url.startswith("https://gateway.example.com/v1/file/-100123/42?exp=")
    assert "&sig=" in url


def test_sdk_thumb_url():
    gw = TelegramGateway(GATEWAY_URL, secret=SECRET)
    assert gw.get_thumb_url(-100123, 42) == "https://gateway.example.com/v1/thumb/-100123/42"


def test_sdk_unsigned_urls():
    gw = TelegramGateway(GATEWAY_URL, secret=None)
    assert gw.get_stream_url(-100123, 42) == "https://gateway.example.com/v1/stream/-100123/42"
    assert gw.get_download_url(-100123, 42) == "https://gateway.example.com/v1/file/-100123/42"
