import asyncio
from unittest.mock import AsyncMock

import pytest

from app.main import _client_holder, _get_client
from app.telegram_stream import _resolve_media


class Client:
    def __init__(self, authorized=True):
        self.connected = False
        self.authorized = authorized
        self.calls = 0

    def is_connected(self):
        return self.connected

    async def connect(self):
        self.calls += 1
        await asyncio.sleep(0)
        self.connected = True

    async def is_user_authorized(self):
        return self.authorized

    async def disconnect(self):
        self.connected = False


def test_reconnects_disconnected_client_once():
    asyncio.run(_test_reconnect())


async def _test_reconnect():
    client = Client()
    holder = _client_holder()
    holder['client'] = client
    assert await asyncio.gather(*(_get_client(None, holder) for _ in range(3))) == [client] * 3
    assert client.calls == 1


def test_invalid_session_does_not_remain_cached():
    asyncio.run(_test_invalid_session())


async def _test_invalid_session():
    client = Client(authorized=False)
    holder = _client_holder()
    holder['client'] = client
    with pytest.raises(RuntimeError, match='not authorized'):
        await _get_client(None, holder)
    assert holder['client'] is None


def test_disconnected_resolution_does_not_become_not_found():
    asyncio.run(_test_disconnected_resolution())


async def _test_disconnected_resolution():
    client = Client()
    client.get_entity = AsyncMock(side_effect=ConnectionError('disconnected'))
    with pytest.raises(ConnectionError):
        await _resolve_media(client, 1, 2)
