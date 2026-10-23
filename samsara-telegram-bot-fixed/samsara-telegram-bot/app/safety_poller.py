from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import Settings
from app.formatters import extract_media_urls, format_safety_event
from app.publisher import Publisher
from app.samsara_client import SamsaraClient

logger = logging.getLogger(__name__)


def _redact(obj: Any) -> Any:
    """Hide video/download URLs (they are signed links) before logging a sample event."""
    if isinstance(obj, dict):
        return {
            k: ("<url>" if "url" in k.lower() and isinstance(v, str) else _redact(v)) for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    return obj


class SafetyPoller:
    def __init__(
        self,
        settings: Settings,
        samsara: SamsaraClient,
        publisher: Publisher,
    ) -> None:
        self.settings = settings
        self.samsara = samsara
        self.publisher = publisher
        self.tz = settings.display_tz()
        self._seen: set[str] = set()
        self._cursor: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._sample_logged = False

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="safety-poller")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except Exception:
                self._task.cancel()

    async def _run(self) -> None:
        logger.info("Safety poller started (every %ss)", self.settings.safety_poll_seconds)
        # Seed: ignore backlog older than a few minutes so restart doesn't spam.
        start = datetime.now(timezone.utc) - timedelta(minutes=10)
        while not self._stop.is_set():
            try:
                await self._poll_once(start)
            except Exception:
                logger.exception("Safety poll failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.settings.safety_poll_seconds)
            except asyncio.TimeoutError:
                pass

    async def _poll_once(self, start: datetime) -> None:
        if not self.settings.samsara_api_token:
            return

        payload = await self.samsara.get_safety_events_stream(
            start_time=start,
            after=self._cursor,
        )
        data = payload.get("data") or []
        pagination = payload.get("pagination") or {}
        if pagination.get("endCursor"):
            self._cursor = pagination["endCursor"]

        for event in data:
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("id") or "")
            if not event_id or event_id in self._seen:
                continue
            self._seen.add(event_id)
            # Bound memory
            if len(self._seen) > 5000:
                self._seen = set(list(self._seen)[-2500:])

            if not self._sample_logged:
                # One-time look at the real payload shape (URLs hidden) to help debugging.
                logger.info("Sample safety event: %s", json.dumps(_redact(event))[:1500])
                self._sample_logged = True

            await self._publish_event(event)

    async def _publish_event(self, event: dict[str, Any]) -> None:
        vehicle_name, driver_name = await self.samsara.resolve_event_people(event)
        topic_key, caption = format_safety_event(
            event, vehicle_name=vehicle_name, driver_name=driver_name, tz=self.tz
        )

        blob: bytes | None = None
        for url in extract_media_urls(event)[:1]:
            data = await self.samsara.download_bytes(url)
            if data and len(data) < 48 * 1024 * 1024:
                blob = data

        ok = await self.publisher.publish(topic_key, caption, video=blob)
        logger.info("Published safety event %s -> %s (ok=%s)", event.get("id"), topic_key, ok)
