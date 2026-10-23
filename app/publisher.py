from __future__ import annotations

import logging

from app.telegram_client import TelegramClient
from app.topics import TopicManager

logger = logging.getLogger(__name__)


def _thread_missing(description: str) -> bool:
    return "thread not found" in (description or "").lower()


class Publisher:
    """Sends an alert to the right topic (video if available, otherwise text)."""

    def __init__(self, telegram: TelegramClient, topics: TopicManager) -> None:
        self.telegram = telegram
        self.topics = topics

    async def publish(self, topic_key: str, text: str, *, video: bytes | None = None) -> bool:
        for attempt in range(2):
            thread_id = await self.topics.thread_for(topic_key)
            ok, error = await self._send(thread_id, text, video)
            if ok:
                return True
            if attempt == 0 and thread_id is not None and _thread_missing(error):
                # Topic was deleted in Telegram: forget it and create a fresh one.
                logger.warning("Topic for %s no longer exists; recreating", topic_key)
                self.topics.forget(topic_key)
                continue
            return False
        return False

    async def _send(self, thread_id: int | None, text: str, video: bytes | None) -> tuple[bool, str]:
        if video:
            result = await self.telegram.send_video(video, text, thread_id=thread_id)
            if result.get("ok"):
                return True, ""
            error = str(result.get("description") or "")
            if _thread_missing(error):
                return False, error
            # video failed for another reason: fall back to plain text
        result = await self.telegram.send_message(text, thread_id=thread_id)
        return bool(result.get("ok")), str(result.get("description") or "")
