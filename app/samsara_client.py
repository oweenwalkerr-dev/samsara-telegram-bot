from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
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
        # id -> display name caches (refreshed lazily when an id is missing)
        self._names: dict[str, dict[str, str]] = {"vehicle": {}, "driver": {}}
        self._names_loaded_at: dict[str, float] = {"vehicle": 0.0, "driver": 0.0}

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
        }
        if after:
            params["after"] = after

        # Prefer v2 stream; fall back path names if org still on older API surface.
        for path in ("/safety-events/stream", "/fleet/safety-events"):
            req_params = dict(params)
            if path == "/safety-events/stream":
                # Without these, the stream returns only vehicle/driver IDs (no names).
                req_params["includeAsset"] = "true"
                req_params["includeDriver"] = "true"
            try:
                resp = await self._client.get(path, params=req_params)
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
            # Samsara answers with a 307 redirect to a signed download link; follow it.
            resp = await self._client.get(url, follow_redirects=True)
            resp.raise_for_status()
            return resp.content
        except Exception as exc:
            logger.warning("Media download failed: %s", exc)
            return None

    # ------------------------------------------------------------------ names
    async def _list_all(self, path: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        params: dict[str, str] = {}
        for _ in range(30):
            resp = await self._client.get(path, params=params)
            resp.raise_for_status()
            body = resp.json()
            out.extend(body.get("data") or [])
            page = body.get("pagination") or {}
            if page.get("hasNextPage") and page.get("endCursor"):
                params["after"] = page["endCursor"]
            else:
                break
        return out

    async def _refresh_names(self, kind: str) -> None:
        path = "/fleet/vehicles" if kind == "vehicle" else "/fleet/drivers"
        try:
            rows = await self._list_all(path)
        except Exception as exc:  # missing scope, network, etc. -> keep working without names
            logger.warning("Could not load %s names from %s: %s", kind, path, exc)
            self._names_loaded_at[kind] = time.monotonic()
            return
        self._names[kind] = {str(r["id"]): str(r["name"]) for r in rows if r.get("id") and r.get("name")}
        self._names_loaded_at[kind] = time.monotonic()
        logger.info("Loaded %d %s names", len(self._names[kind]), kind)

    async def _lookup_name(self, kind: str, ident: Any) -> str | None:
        if not ident:
            return None
        ident = str(ident)
        found = self._names[kind].get(ident)
        if found:
            return found
        loaded = self._names_loaded_at[kind]
        # refresh on a miss, at most every 10 minutes
        if loaded == 0.0 or time.monotonic() - loaded > 600:
            await self._refresh_names(kind)
        return self._names[kind].get(ident)

    async def resolve_event_people(self, event: dict[str, Any]) -> tuple[str | None, str | None]:
        """Return (vehicle_name, driver_name) for a safety event.

        Uses names already in the event when present, otherwise looks the ids up.
        Driver is "Unassigned" when the event has no driver.
        """
        asset = event.get("asset") if isinstance(event.get("asset"), dict) else {}
        vehicle = event.get("vehicle") if isinstance(event.get("vehicle"), dict) else {}
        driver = event.get("driver") if isinstance(event.get("driver"), dict) else {}

        vehicle_name = next((d.get("name") for d in (asset, vehicle) if d.get("name")), None)
        if not vehicle_name:
            vehicle_id = next((d.get("id") for d in (asset, vehicle) if d.get("id")), None) or event.get(
                "vehicleId"
            ) or event.get("assetId")
            vehicle_name = await self._lookup_name("vehicle", vehicle_id)

        driver_name = driver.get("name")
        if not driver_name:
            driver_id = driver.get("id") or event.get("driverId")
            # Samsara uses id "0" for "no driver assigned"
            if driver_id and str(driver_id) != "0":
                driver_name = await self._lookup_name("driver", driver_id)
            else:
                driver_name = "Unassigned"
        return vehicle_name, driver_name
