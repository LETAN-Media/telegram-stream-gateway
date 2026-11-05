# telegram-stream-gateway

Universal Telegram Media Infrastructure & Streaming Gateway over MTProto.

Stream large Telegram videos, audios, images, and files over MTProto with standard HTTP Range (`206`), without the 20MB `getFile` limit of the Telegram Bot API cloud endpoint. No full-file buffering, no disk writes, 100% serverless compatible.

```
Telegram --MTProto--> gateway --HTTP Range/206--> Player / Browser / Backend SDK
```

## Features

- **Universal Multi-Website & Multi-Tool Support**: Connect any number of websites, apps, bots, or scripts to any Telegram channel.
- **Cross-Platform SDKs**: Ready-to-use SDKs for JavaScript/TypeScript (Browser, Node.js, Cloudflare Workers) and Python (FastAPI, Flask, scripts, bots) inside `/sdk`.
- **Global CORS Support**: Stream and fetch from any domain without CORS restrictions.
- **Flexible Authentication**:
  - `API Key` / `Bearer token`: Fast and simple for backends, tools, and scripts.
  - `HMAC Signed URLs`: Time-limited (`exp` + `sig`) URLs for secure public browser players without exposing master secrets.
  - `Allow Unsigned`: Optional dev mode for open streaming.
- **Multi-Media Endpoints**:
  - Streaming with HTTP Range (`206 Partial Content`) for Video and Audio players.
  - Direct download links (`Content-Disposition: attachment`).
  - Channel Feed parser with media type filters (`video`, `audio`, `photo`, `document`, `all`) and pagination.
  - Dynamic thumbnails and posters extractor.
  - Channel / Group inspector (`/v1/chats`).

## SDK Quick Start

See detailed code samples in [EXAMPLES.md](./EXAMPLES.md).

### JavaScript / TypeScript / Node.js / Cloudflare Workers
```javascript
const TelegramGateway = require('./sdk/telegram-gateway.js');

const gw = new TelegramGateway({
  gatewayUrl: 'https://your-gateway.code.run',
  secret: 'YOUR_SECRET_KEY'
});

// Fetch channel feed
const feed = await gw.getFeed(-1003952062608, { type: 'video' });

// Generate signed stream URL for HTML5 video
const streamUrl = await gw.getStreamUrl(-1003952062608, 56, 21600);
```

### Python SDK
```python
from sdk.telegram_gateway import TelegramGateway

gw = TelegramGateway('https://your-gateway.code.run', secret='YOUR_SECRET_KEY')

# List all accessible channels
chats = gw.list_chats()

# Fetch latest media
feed = gw.get_feed(-1003952062608, media_type='video')
```

## Endpoints

| Method | Endpoint | Description | Auth |
| :--- | :--- | :--- | :--- |
| `GET` | `/healthz` | Health check & Telegram connection status | None |
| `GET` | `/v1/chats` | List all dialogs and channels with their IDs | API Key / Bearer |
| `GET` | `/v1/feed/{chat_id}` | Channel feed (params: `limit`, `offset_id`, `type`) | Allowed Chat or API Key |
| `GET` | `/v1/info/{chat_id}/{message_id}` | Metadata (size, mime, filename, caption, date) | API Key or HMAC |
| `GET`/`HEAD` | `/v1/stream/{chat_id}/{message_id}` | Stream media with Range 206 support | API Key or HMAC |
| `GET`/`HEAD` | `/v1/video/{chat_id}/{message_id}` | Legacy video streaming alias | API Key or HMAC |
| `GET`/`HEAD` | `/v1/file/{chat_id}/{message_id}` | Download file (attachment) with Range 206 | API Key or HMAC |
| `GET` | `/v1/thumb/{chat_id}/{message_id}` | Thumbnail / poster image | Allowed Chat or API Key |

## Configuration (Environment Variables)

### Core Telegram Credentials
- `TELEGRAM_API_ID`: Telegram API ID from `my.telegram.org`.
- `TELEGRAM_API_HASH`: Telegram API Hash.
- `TELEGRAM_SESSION_STRING`: Telethon session string (recommended for 2GB-4GB uploads and channel access).
- `TELEGRAM_BOT_TOKEN`: Optional alternative if using a Bot account.

### Security & Multi-Channel
- `STREAM_SIGNING_SECRET`: Master secret key used for HMAC URL signing and API Key authentication.
- `ALLOWED_CHAT_IDS`: Comma-separated list of allowed chat IDs (e.g. `-1003952062608,-100123456789`) or `*` to allow all channels.
- `ALLOW_ALL_CHATS`: Set to `true` to allow any channel accessible by the Telegram account.
- `ALLOW_UNSIGNED_STREAMS`: Set to `true` only for local dev. Default is `false`.

### Performance & Limits
- `MAX_CONCURRENT_STREAMS`: Maximum concurrent MTProto download streams (default `4`).
- `TELEGRAM_CHUNK_SIZE`: Chunk size in bytes (default `524288` / 512KB, multiple of 4096).
- `REQUEST_TIMEOUT_SECONDS`: Chunk timeout in seconds (default `30.0`).
- `PORT`: HTTP port (default `8080`).

## Testing

```bash
.venv/bin/pytest tests/ -v
```
