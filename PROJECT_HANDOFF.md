# PROJECT HANDOFF — TELEGRAM STREAM GATEWAY

> Cập nhật: 2026-09-20 ~22:2x UTC (Codebuff session 2). Không chứa secret.

## TRẠNG THÁI MỚI NHẤT (session 2)

- Commit `d86fee1` "r2 persistent cache: background mirror + cache trigger/status endpoints"
  đã PUSH main (Northflank CI/CD tự deploy, live xác nhận qua /v1/cache-status 404→403).
- Bug `object_key` 3-args đã FIX (2 args đồng bộ) — cache-status production trả 200/MISS, KHÔNG 500.
- Regression streaming cũ: KHÔNG (HEAD 200 + Range 206 start/end qua worker đã verify sau deploy).
- **R2 ENV ĐÃ SET (2026-09-20 22:23 UTC)** qua Northflank API POST /runtime-environment
  (4 biến R2_NEW_* — không in giá trị). Service redeploy COMPLETED, healthz xanh.
- **MIRROR E2E PASS:** mid=25 (92MB): STARTED → CACHING → READY ~4 phút; mid=23 (180MB) READY
  (trigger tự động từ Worker khi R2 MISS). R2 ContentLength == Telegram size cả hai.
  R2 metadata đầy đủ (chat/message/file_size/filename/cached_at/storage_source).
- Worker (web-phim e23d276+) giờ gọi POST /v1/cache khi R2 MISS — flow persistent cache hoàn chỉnh.
- Pre-cache CLI bên web repo: scripts/cache_video.py (signed, --wait).

## Vai trò

FastAPI + Telethon MTProto gateway: stream video Telegram >20MB qua HTTP Range/206
cho Cloudflare Worker web-phim. Chạy trên Northflank (Dockerfile, port 8080, /healthz).
Telegram = master storage. R2 mirror = cache nền (đang làm dở, xem dưới).

## Repo / git

- Repo: `LETAN-Media/telegram-stream-gateway` (origin https)
- Branch: `main`; local HEAD `eb35f87` "startup MTProto connect+warm, 30m metadata cache..."
  = origin/main theo ref local (fetch fails trong session này: không có gh/git credential).
- **UNCOMMITTED (quan trọng):**
  - `app/r2_mirror.py` (untracked) — MirrorManager, multipart upload 8MB parts, retry/backoff,
    dedup lock per-video, size verify, abort multipart khi lỗi, key `videos/<chat>/<mid>.mp4`
  - `app/main.py` (modified) — thêm POST `/v1/cache/{chat}/{mid}` (trigger) và
    GET `/v1/cache-status/{chat}/{mid}` (state: READY/CACHING/MISS/FAILED/SKIPPED)
  - `requirements.txt` (modified) — +boto3
  - `tests/test_mirror.py` (untracked) — unit test key/dedup/settings
- Lịch sử: `c28c080` fix Telethon 1.45 GetFileRequest (live 206 verified), `97f8bfc` MTProto
  range streaming + signed URLs.

## Production state

- Northflank build từ GitHub main. Public URL:
  https://p01--telegram-stream-gateway--r4v2pzg2klvq.code.run
- `/healthz` → {"ok":true,"telegram_connected":true,"telegram_configured":true} (2026-09-20 21:43 UTC)
- `/v1/cache-status/...` → 404 → **code mirror CHƯA ở production**.
- Không xác định được commit production từ ngoài (Northflank dashboard cần check thủ công);
  streaming hoạt động đúng nên production ổn ở mức c28c080/eb35f87.

## Đã PASS

- MTProto streaming, HEAD, Range start/middle/end (suffix), 206 exact — test qua worker
  production trên video 92MB (mid=25) lúc 21:43 UTC 2026-09-20.
- pytest local: 35/35 PASS (test_mirror đã chạy cùng bộ).

## R2 mirror — trạng thái chi tiết

- R2_LOOKUP_IMPLEMENTED (ở gateway cache-status): YES (ĐÃ DEPLOY, verified: 200/MISS)
- BACKGROUND_MIRROR_IMPLEMENTED: YES (ĐÃ DEPLOY; DISABLED do thiếu ENV runtime)
- MULTIPART_IMPLEMENTED: YES (8MB parts + retry + abort-on-error, đã deploy)
- UPLOAD_DEDUP_IMPLEMENTED: YES (lock + head-check trước upload, đã deploy)
- SIZE_VERIFY_IMPLEMENTED: YES (byte count + head sau complete, đã deploy)
- End-to-end (trigger thật → object READY trên R2): CHỜ ENV — chưa chạy được.
- Worker hiện chưa gọi 2 endpoint này — flow worker-side (lookup + trigger) là STEP 5.

## ENV cần cho mirror (Northflank dashboard / .env, KHÔNG commit)

- R2_NEW_ENDPOINT (hoặc fallback R2_ENDPOINT): MISSING trên local .env → phải thêm trước deploy
- R2_NEW_ACCESS_KEY_ID (hoặc R2_ACCESS_KEY_ID): MISSING trên local .env
- R2_NEW_SECRET_ACCESS_KEY (hoặc R2_SECRET_ACCESS_KEY): MISSING trên local .env
- R2_NEW_BUCKET (hoặc R2_BUCKET): MISSING trên local .env
- R2_CACHE_ENABLED (mặc định true), R2_MAX_CACHE_FILE_SIZE (0 = unlimited): optional
- Creds R2 đang nằm ở /root/web-phim/.env (R2_*) — account chứa bucket web-phim-videos.

ENV streaming hiện tại: TELEGRAM_API_ID/HASH, TELEGRAM_BOT_TOKEN, TELEGRAM_SESSION_STRING,
ALLOWED_CHAT_IDS, STREAM_SIGNING_SECRET: SET. ALLOW_UNSIGNED_STREAMS=false (đúng).

## Next steps

1. Fix `object_key` signature (đồng bộ 2 nơi), pytest lại.
2. Commit + push `r2_mirror.py`, `main.py`, `requirements.txt`, `test_mirror.py` → Northflank CI.
3. Thêm R2 creds vào Northflank ENV.
4. Test thật: POST /v1/cache/<chat>/25 → poll /v1/cache-status → READY → verify object trên R2.
5. Sau đó làm R2 lookup/fallback ở worker (repo web-phim).

## Rollback

- Northflank redeploy commit `c28c080` (206 verified live). Không xóa session string/token.
- Mirror chỉ ghi R2 bucket mới `web-phim-videos` (prefix `videos/<chat>/<mid>.mp4`), không
  đụng R2 account cũ, không xóa Telegram data.
