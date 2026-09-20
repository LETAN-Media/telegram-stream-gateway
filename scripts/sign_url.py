#!/usr/bin/env python3
"""Generate a signed video URL for local testing.

Usage:
  STREAM_SIGNING_SECRET=... python3 scripts/sign_url.py \
      --base http://127.0.0.1:8080 --chat -100123 --msg 25 --ttl 3600
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.security import build_signed_query


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--chat", required=True, type=int)
    parser.add_argument("--msg", required=True, type=int)
    parser.add_argument("--ttl", default=3600, type=int)
    args = parser.parse_args()

    secret = (os.environ.get("STREAM_SIGNING_SECRET") or "").strip()
    if not secret:
        print("missing ENV STREAM_SIGNING_SECRET", flush=True)
        sys.exit(1)
    query = build_signed_query(args.chat, args.msg, args.ttl, secret)
    base = args.base.rstrip("/")
    print(f"{base}/v1/video/{args.chat}/{args.msg}?{query}", flush=True)


if __name__ == "__main__":
    main()
