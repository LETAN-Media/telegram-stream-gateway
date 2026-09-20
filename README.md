# telegram-stream-gateway

Stream large Telegram videos over MTProto with standard HTTP Range (`206`),
without the 20MB `getFile` limit of the Telegram Bot API cloud endpoint.
No full-file buffering, no disk writes, no R2 in this phase.

```
Telegram --MTProto--> gateway --HTTP Range/206--> Northflank --> Cloudflare --> player
```

**Why Telethon (not Pyrogram):** Telethon exposes raw `upload.GetFile`
with explicit `offset`/`limit`, so Range requests can be aligned to
Telegram chunk boundaries manually and trimmed byte-exactly. It also
supports non-interactive `StringSession` and bot-token login, which fits
a headless Northflank container. Pyrogram's higher-level downloader hides
offset control, making exact `Content-Length` guarantees harder.

## Endpoints

- `GET /healthz` — liveness, always 200. Includes `telegram_connected`.
- `GET /v1/video/{chat_id}/{message_id}?exp=...&sig=...` — video stream.
- `HEAD /v1/video/{chat_id}/{message_id}?exp=...&sig=...` — metadata only.

Auth: `ALLOWED_CHAT_IDS` allowlist + HMAC-SHA256 signed query
(`sig` over `chat_id:message_id:exp`). Set `ALLOW_UNSIGNED_STREAMS=true`
only for local testing; default is `false` (deny unsigned).

Generate a signed URL:

```sh
STREAM_SIGNING_SECRET=... python3 scripts/sign_url.py \
  --base http://127.0.0.1:8080 --chat -100123 --msg 25 --ttl 3600
```

Create a session string once (interactive, on a trusted machine):

```sh
python3 scripts/create_telegram_session.py
```

## Local dev

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests/ -q
uvicorn app.main:app --host 127.0.0.1 --port 8080
```

## Docker

```sh
docker build -t telegram-stream-gateway .
docker run --rm -p 8080:8080 --env-file .env telegram-stream-gateway
curl -s localhost:8080/healthz
```

## NORTHFLANK_DEPLOYMENT

- Repository: `LETAN-Media/telegram-stream-gateway` (private)
- Build: `Dockerfile`
- Port: `8080` (or `$PORT`)
- Health check: `/healthz`

Required ENV (values set in Northflank dashboard, never committed):

- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- `TELEGRAM_BOT_TOKEN` or `TELEGRAM_SESSION_STRING`
- `ALLOWED_CHAT_IDS`
- `STREAM_SIGNING_SECRET`
- `ALLOW_UNSIGNED_STREAMS=false`

Optional ENV:

- `MAX_CONCURRENT_STREAMS` (default 4)
- `TELEGRAM_CHUNK_SIZE` (default 524288, must be multiple of 4096)
- `REQUEST_TIMEOUT_SECONDS` (default 30)
- `LOG_LEVEL` (default info)
