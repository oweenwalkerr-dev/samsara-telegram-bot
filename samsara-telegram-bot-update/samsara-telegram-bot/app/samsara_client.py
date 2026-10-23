from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


class SamsaraClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.samsara_base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {settings.samsara_api_token}",
                "Accept": "application/json",
            },
            timeout=30.0,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def verify_webhook_signature(
        self,
        *,
        timestamp: str,
        body: bytes,
        signature_header: str,
    ) -> bool:
        """Verify Samsara webhook HMAC signature (v1).

        Message format: v1:<timestamp>:<body>
        Header format:  v1=<hex_hmac>
        Secret is base64-encoded in the dashboard.
        """
        secret = (self.settings.samsara_webhook_secret or "").strip()
        if not secret:
            # Allow first deploys without secret; enable once webhook is created.
            logger.warning("SAMSARA_WEBHOOK_SECRET empty — skipping signature check")
            return True

        try:
            key = base64.b64decode(secret)
        except Exception:
            # Some accounts paste the raw secret string
            key = secret.encode("utf-8")

        expected_prefix = "v1="
        if not signature_header.startswith(expected_prefix):
            return False

        provided = signature_header[len(expected_prefix) :]
        message = f"v1:{timestamp}:{body.decode('utf-8')}".encode("utf-8")
        digest = hmac.new(key, message, hashlib.sha256).hexdigest()
        return hmac.compare_digest(digest, provided)

    async def get_safety_events_stream(
        self,
        *,
        start_time: datetime | None = None,
        after: str | None = None,
    ) -> dict[str, Any]:
        if start_time is None:
            start_time = datetime.now(timezone.utc) - timedelta(minutes=30)

        params: dict[str, str] = {
            "startTime": start_time.isoformat().replace("+00:00", "Z"),
            "includeAsset": "true",
            "includeDriver": "true",
        }
        if after:
            params["after"] = after

        # Prefer v2 stream; fall back path names if org still on older API surface.
        for path in ("/safety-events/stream", "/fleet/safety-events"):
            try:
                resp = await self._client.get(path, params=params)
                if resp.status_code == 400 and ("includeAsset" in params or "includeDriver" in params):
                    slim = {k: v for k, v in params.items() if k not in ("includeAsset", "includeDriver")}
                    resp = await self._client.get(path, params=slim)
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                logger.warning("Samsara %s failed: %s", path, exc)
                continue
        return {"data": [], "pagination": {}}

    async def download_bytes(self, url: str) -> bytes | None:
        try:
            resp = await self._client.get(url, follow_redirects=True)
            resp.raise_for_status()
            return resp.content
        except Exception as exc:
            logger.warning("Media download failed: %s", exc)
            return None
