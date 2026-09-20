#!/usr/bin/env python3
"""Create a Telegram StringSession ONCE on the VPS (interactive login).

Run manually:  python3 scripts/create_telegram_session.py
Requires TELEGRAM_API_ID + TELEGRAM_API_HASH in env.
For bot sessions, prefer TELEGRAM_BOT_TOKEN directly (no script needed).
Prints the session string once — store it as TELEGRAM_SESSION_STRING
in Northflank ENV. Never commit it.
"""
from __future__ import annotations

import asyncio
import os
import sys


def _get(name: str) -> str:
    v = (os.environ.get(name) or "").strip()
    if not v:
        print(f"missing ENV {name}", flush=True)
        sys.exit(1)
    return v


async def _main() -> None:
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError:
        print("telethon not installed. run: pip install telethon", flush=True)
        sys.exit(2)

    api_id = int(_get("TELEGRAM_API_ID"))
    api_hash = _get("TELEGRAM_API_HASH")
    bot_token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()

    async with TelegramClient(StringSession(), api_id, api_hash) as client:
        if bot_token:
            await client.sign_in(bot_token=bot_token)
        else:
            await client.start()  # interactive phone/code login
        me = await client.get_me()
        name = getattr(me, "username", None) or getattr(me, "first_name", "?")
        print(f"authorized as: {name}", flush=True)
        print("=" * 60, flush=True)
        print(client.session.save(), flush=True)
        print("=" * 60, flush=True)
        print("Store the string above as TELEGRAM_SESSION_STRING. Do not commit it.", flush=True)


if __name__ == "__main__":
    asyncio.run(_main())
