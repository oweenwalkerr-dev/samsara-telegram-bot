from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import Settings
from app.formatters import extract_media_urls, format_safety_event
from app.samsara_client import SamsaraClient
from app.telegram_client import TelegramClient

logger = logging.getLogger(__name__)


class SafetyPoller:
    def __init__(
        self,
        settings: Settings,
        samsara: SamsaraClient,
        telegram: TelegramClient,
    ) -> None:
        self.settings = settings
        self.samsara = samsara
        self.telegram = telegram
        self._seen: set[str] = set()
        self._cursor: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

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

            await self._publish_event(event)

    async def _publish_event(self, event: dict[str, Any]) -> None:
        topic_key, caption = format_safety_event(event)
        topics = self.settings.topic_ids()
        thread_id = topics.get(topic_key) or topics.get("General")

        media_urls = extract_media_urls(event)
        sent = False
        for url in media_urls[:1]:
            blob = await self.samsara.download_bytes(url)
            if blob and len(blob) < 48 * 1024 * 1024:
                result = await self.telegram.send_video(blob, caption, thread_id=thread_id)
                sent = bool(result.get("ok"))
                if sent:
                    break

        if not sent:
            await self.telegram.send_message(caption, thread_id=thread_id)
        logger.info("Published safety event %s -> %s", event.get("id"), topic_key)
