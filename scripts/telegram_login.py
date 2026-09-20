#!/usr/bin/env python3
"""Two-step Telegram user login for VPS (no interactive stdin).

  python3 scripts/telegram_login.py request --phone +84xxx
  python3 scripts/telegram_login.py confirm --code 12345 [--password ...]

State kept in /tmp/tsg-login.json (0600). Session file login_user.session
is deleted on failure; on success print SAVE-AS-SESSION (handle with care).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

STATE = "/tmp/tsg-login.json"
SESSION_NAME = "login_user"


def _env(name: str) -> str:
    v = (os.environ.get(name) or "").strip()
    if not v:
        print(f"missing ENV {name}", flush=True)
        sys.exit(1)
    return v


def _load_env_file() -> None:
    for path in ("/root/telegram-stream-gateway/.env", "/root/web-phim/.env"):
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


def _save_state(data: dict) -> None:
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.chmod(STATE, 0o600)


def _load_state() -> dict:
    with open(STATE, encoding="utf-8") as f:
        return json.load(f)


async def _request(phone: str) -> None:
    from telethon import TelegramClient

    api_id = int(_env("TELEGRAM_API_ID"))
    api_hash = _env("TELEGRAM_API_HASH")
    client = TelegramClient(SESSION_NAME, api_id, api_hash)
    await client.connect()
    if await client.is_user_authorized():
        print("ALREADY_AUTHORIZED", flush=True)
        await client.disconnect()
        return
    sent = await client.send_code_request(phone)
    _save_state({"phone": phone, "phone_code_hash": sent.phone_code_hash})
    print("CODE_SENT", flush=True)
    await client.disconnect()


async def _confirm(code: str, password: str | None) -> None:
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    api_id = int(_env("TELEGRAM_API_ID"))
    api_hash = _env("TELEGRAM_API_HASH")
    state = _load_state()
    client = TelegramClient(SESSION_NAME, api_id, api_hash)
    await client.connect()
    try:
        await client.sign_in(
            phone=state["phone"], code=code, phone_code_hash=state["phone_code_hash"]
        )
    except Exception as e:
        if type(e).__name__ == "SessionPasswordNeededError" and password:
            await client.sign_in(password=password)
        else:
            raise
    me = await client.get_me()
    print(f"AUTHORIZED id={me.id}", flush=True)
    print("SAVE-AS-SESSION:" + StringSession.save(client.session), flush=True)
    await client.disconnect()


def main() -> None:
    _load_env_file()
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("request")
    p1.add_argument("--phone", required=True)
    p2 = sub.add_parser("confirm")
    p2.add_argument("--code", required=True)
    p2.add_argument("--password", default=None)
    args = parser.parse_args()
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if args.cmd == "request":
        asyncio.run(_request(args.phone))
    else:
        asyncio.run(_confirm(args.code, args.password))


if __name__ == "__main__":
    main()
