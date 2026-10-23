from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import Settings
from app.formatters import extract_media_urls, format_safety_event
from app.samsara_client import SamsaraClient
from app.telegram_client import TelegramClient
from app.topics import effective_topic_ids

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
        # Look back far enough to resend events that failed while Telegram was broken.
        start = datetime.now(timezone.utc) - timedelta(hours=6)
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
        page_ok = True

        for event in data:
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("id") or "")
            if not event_id or event_id in self._seen:
                continue
            if await self._publish_event(event):
                self._seen.add(event_id)
            else:
                page_ok = False

        if len(self._seen) > 5000:
            self._seen = set(list(self._seen)[-2500:])

        # Only advance the cursor when every event on this page was delivered.
        if page_ok and pagination.get("endCursor"):
            self._cursor = pagination["endCursor"]

    async def _publish_event(self, event: dict[str, Any]) -> bool:
        topic_key, caption = format_safety_event(event)
        topics = effective_topic_ids(self.settings.topic_ids())
        thread_id = topics.get(topic_key) or topics.get("General")

        media_urls = extract_media_urls(event)
        for url in media_urls[:1]:
            blob = await self.samsara.download_bytes(url)
            if blob and len(blob) < 48 * 1024 * 1024:
                result = await self.telegram.send_video(blob, caption, thread_id=thread_id)
                if result.get("ok"):
                    logger.info("Published safety event %s -> %s (video)", event.get("id"), topic_key)
                    return True

        result = await self.telegram.send_message(caption, thread_id=thread_id)
        if result.get("ok"):
            logger.info("Published safety event %s -> %s", event.get("id"), topic_key)
            return True
        logger.error("Telegram rejected safety event %s: %s", event.get("id"), result)
        return False
