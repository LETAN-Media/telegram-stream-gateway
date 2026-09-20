#!/usr/bin/env python3
"""One-off e2e mirror test. Prints ONLY states/HTTP codes — never secrets or signed URLs."""
import json
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, "/root/telegram-stream-gateway")
from app.security import build_signed_query  # noqa: E402

GATEWAY = "https://p01--telegram-stream-gateway--r4v2pzg2klvq.code.run"
CHAT = "-1003952062608"
MID = 25
POLL_INTERVAL = 15
MAX_POLLS = 36


def load_secret():
    for line in open("/root/telegram-stream-gateway/.env", encoding="utf-8"):
        line = line.strip()
        if line.startswith("STREAM_SIGNING_SECRET="):
            return line.split("=", 1)[1].strip().strip("'\"")
    return None


def http(url, method="GET"):
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode() or "{}")
        except Exception:
            body = {}
        return e.code, body
    except Exception as e:
        return 0, {"error": type(e).__name__}


def main():
    secret = load_secret()
    if not secret:
        print("FATAL: STREAM_SIGNING_SECRET not found in gateway .env")
        sys.exit(2)
    q = build_signed_query(int(CHAT), MID, 3600, secret)

    status, trig = http(f"{GATEWAY}/v1/cache/{CHAT}/{MID}?{q}", method="POST")
    print(f"TRIGGER http={status} cache={trig.get('cache')}")

    state = None
    for i in range(1, MAX_POLLS + 1):
        status, st = http(f"{GATEWAY}/v1/cache-status/{CHAT}/{MID}?{q}")
        state = st.get("state")
        print(f"poll {i}: http={status} state={state} size={st.get('size')} error={st.get('error')}")
        if state in ("READY", "FAILED", "SKIPPED"):
            break
        if status == 0 or status >= 500:
            print("HTTP 5xx — aborting poll")
            sys.exit(3)
        time.sleep(POLL_INTERVAL)

    print(f"FINAL state={state}")
    sys.exit(0 if state == "READY" else 4)


if __name__ == "__main__":
    main()
