"""Telegram Media Gateway - Python SDK.

Zero external dependencies (uses standard library urllib, hmac, hashlib, json).
Can be used in any Python backend, script, scraper, or bot.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional


class GatewayError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code
        self.message = message


class TelegramGateway:
    """Client for telegram-stream-gateway."""

    def __init__(self, gateway_url: str, secret: Optional[str] = None):
        self.gateway_url = gateway_url.rstrip("/")
        self.secret = secret

    def _sign(self, chat_id: int, message_id: int, exp: int) -> str:
        if not self.secret:
            raise ValueError("secret required to generate HMAC signature")
        msg = f"{chat_id}:{message_id}:{exp}".encode()
        return hmac.new(self.secret.encode(), msg, hashlib.sha256).hexdigest()

    def get_stream_url(self, chat_id: int, message_id: int, ttl_seconds: int = 21600) -> str:
        """Generate a signed streaming URL for HTML5 <video> / <audio> tags."""
        if not self.secret:
            return f"{self.gateway_url}/v1/stream/{chat_id}/{message_id}"
        exp = int(time.time()) + ttl_seconds
        sig = self._sign(chat_id, message_id, exp)
        return f"{self.gateway_url}/v1/stream/{chat_id}/{message_id}?exp={exp}&sig={sig}"

    def get_download_url(self, chat_id: int, message_id: int, ttl_seconds: int = 21600) -> str:
        """Generate a direct file download link (Content-Disposition: attachment)."""
        if not self.secret:
            return f"{self.gateway_url}/v1/file/{chat_id}/{message_id}"
        exp = int(time.time()) + ttl_seconds
        sig = self._sign(chat_id, message_id, exp)
        return f"{self.gateway_url}/v1/file/{chat_id}/{message_id}?exp={exp}&sig={sig}"

    def get_thumb_url(self, chat_id: int, message_id: int) -> str:
        """Get the direct thumbnail/poster URL."""
        return f"{self.gateway_url}/v1/thumb/{chat_id}/{message_id}"

    def _request_json(self, path: str, query: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.gateway_url}{path}"
        if query:
            q_clean = {k: v for k, v in query.items() if v is not None}
            url = f"{url}?{urllib.parse.urlencode(q_clean)}"

        headers = {"User-Agent": "TelegramGateway-PythonSDK/2.0"}
        if self.secret:
            headers["X-API-Key"] = self.secret

        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30.0) as resp:
                data = resp.read()
                return json.loads(data.decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8")
                err_data = json.loads(body)
                msg = err_data.get("error", body)
            except Exception:
                msg = str(e)
            raise GatewayError(e.code, msg) from None

    def list_chats(self) -> List[Dict[str, Any]]:
        """List all Telegram channels, groups, and dialogs accessible to the gateway."""
        res = self._request_json("/v1/chats")
        return res.get("chats", [])

    def get_feed(
        self,
        chat_id: int,
        limit: int = 100,
        offset_id: int = 0,
        media_type: str = "video",
    ) -> List[Dict[str, Any]]:
        """Fetch media items from a Telegram channel.
        
        media_type can be: "video", "audio", "photo", "document", or "all".
        """
        query = {"limit": limit, "offset_id": offset_id, "type": media_type}
        res = self._request_json(f"/v1/feed/{chat_id}", query=query)
        return res.get("items", [])

    def get_info(self, chat_id: int, message_id: int) -> Dict[str, Any]:
        """Fetch metadata for a specific media message."""
        query = {}
        if self.secret:
            query["api_key"] = self.secret
        return self._request_json(f"/v1/info/{chat_id}/{message_id}", query=query)

    def download_file(
        self,
        chat_id: int,
        message_id: int,
        output_path: str,
        chunk_size: int = 512 * 1024,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> str:
        """Stream and save a Telegram file directly to disk without loading into RAM."""
        url = self.get_download_url(chat_id, message_id)
        req = urllib.request.Request(url, headers={"User-Agent": "TelegramGateway-PythonSDK/2.0"})
        with urllib.request.urlopen(req, timeout=60.0) as resp:
            total_size = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            with open(output_path, "wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        progress_callback(downloaded, total_size)
        return output_path
