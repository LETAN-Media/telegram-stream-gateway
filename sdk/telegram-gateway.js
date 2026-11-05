/**
 * Telegram Media Gateway - Universal JavaScript / TypeScript SDK.
 * Compatible with: Node.js (>=16), Cloudflare Workers, Browser, Deno, Bun.
 */

(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else if (typeof define === "function" && define.amd) {
    define([], factory);
  } else {
    root.TelegramGateway = factory();
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  async function computeHmacSha256(secret, message) {
    // If Web Crypto API is available (Browsers, Workers, Node 16+)
    if (typeof crypto !== "undefined" && crypto.subtle) {
      const enc = new TextEncoder();
      const key = await crypto.subtle.importKey(
        "raw",
        enc.encode(secret),
        { name: "HMAC", hash: "SHA-256" },
        false,
        ["sign"]
      );
      const sig = await crypto.subtle.sign("HMAC", key, enc.encode(message));
      return Array.from(new Uint8Array(sig))
        .map((b) => b.toString(16).padStart(2, "0"))
        .join("");
    }
    // Fallback for older Node.js
    try {
      const nodeCrypto = require("crypto");
      return nodeCrypto.createHmac("sha256", secret).update(message).digest("hex");
    } catch (e) {
      throw new Error("No cryptographic provider found for HMAC-SHA256.");
    }
  }

  class TelegramGateway {
    /**
     * @param {Object} options
     * @param {string} options.gatewayUrl - Base URL of the deployed gateway (e.g. https://gateway.code.run)
     * @param {string} [options.secret] - STREAM_SIGNING_SECRET / API Key
     */
    constructor({ gatewayUrl, secret = null }) {
      if (!gatewayUrl) throw new Error("gatewayUrl is required");
      this.gatewayUrl = gatewayUrl.replace(/\/+$/, "");
      this.secret = secret;
    }

    /**
     * Sign an arbitrary (chatId, messageId, exp) tuple.
     */
    async sign(chatId, messageId, exp) {
      if (!this.secret) throw new Error("secret is required to sign URLs");
      return await computeHmacSha256(this.secret, `${chatId}:${messageId}:${exp}`);
    }

    /**
     * Generate a signed media streaming URL for HTML5 <video> / <audio> or HLS/DASH.
     * @param {number|string} chatId
     * @param {number|string} messageId
     * @param {number} [ttlSeconds=21600]
     */
    async getStreamUrl(chatId, messageId, ttlSeconds = 21600) {
      if (!this.secret) {
        return `${this.gatewayUrl}/v1/stream/${chatId}/${messageId}`;
      }
      const exp = Math.floor(Date.now() / 1000) + ttlSeconds;
      const sig = await this.sign(chatId, messageId, exp);
      return `${this.gatewayUrl}/v1/stream/${chatId}/${messageId}?exp=${exp}&sig=${sig}`;
    }

    /**
     * Generate a direct file download link (Content-Disposition: attachment).
     */
    async getDownloadUrl(chatId, messageId, ttlSeconds = 21600) {
      if (!this.secret) {
        return `${this.gatewayUrl}/v1/file/${chatId}/${messageId}`;
      }
      const exp = Math.floor(Date.now() / 1000) + ttlSeconds;
      const sig = await this.sign(chatId, messageId, exp);
      return `${this.gatewayUrl}/v1/file/${chatId}/${messageId}?exp=${exp}&sig=${sig}`;
    }

    /**
     * Get the direct thumbnail/poster URL for a media item.
     */
    getThumbUrl(chatId, messageId) {
      return `${this.gatewayUrl}/v1/thumb/${chatId}/${messageId}`;
    }

    /**
     * Internal request helper.
     */
    async _request(path, query = {}) {
      const q = new URLSearchParams();
      for (const [k, v] of Object.entries(query)) {
        if (v !== undefined && v !== null) q.append(k, String(v));
      }
      const qs = q.toString() ? `?${q.toString()}` : "";
      const headers = { "User-Agent": "TelegramGateway-JSSDK/2.0" };
      if (this.secret) headers["X-API-Key"] = this.secret;

      const resp = await fetch(`${this.gatewayUrl}${path}${qs}`, { headers });
      if (!resp.ok) {
        let errMessage = `HTTP ${resp.status}`;
        try {
          const body = await resp.json();
          if (body && body.error) errMessage = body.error;
        } catch (_) {}
        throw new Error(`Gateway Error (${resp.status}): ${errMessage}`);
      }
      return await resp.json();
    }

    /**
     * List all Telegram channels and groups accessible to the gateway.
     */
    async listChats() {
      const res = await this._request("/v1/chats");
      return res.chats || [];
    }

    /**
     * Fetch media items from a channel.
     * @param {number|string} chatId
     * @param {Object} [options]
     * @param {number} [options.limit=100]
     * @param {number} [options.offsetId=0]
     * @param {"video"|"audio"|"photo"|"document"|"all"} [options.type="video"]
     */
    async getFeed(chatId, { limit = 100, offsetId = 0, type = "video" } = {}) {
      const res = await this._request(`/v1/feed/${chatId}`, {
        limit,
        offset_id: offsetId,
        type,
      });
      return res.items || [];
    }

    /**
     * Get metadata for a specific message/media file.
     */
    async getInfo(chatId, messageId) {
      const query = this.secret ? { api_key: this.secret } : {};
      return await this._request(`/v1/info/${chatId}/${messageId}`, query);
    }
  }

  return TelegramGateway;
});
