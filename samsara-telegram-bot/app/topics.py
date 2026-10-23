from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from app.config import Settings
from app.telegram_client import TelegramClient

logger = logging.getLogger(__name__)

# topic key (used in TOPIC_MAP / formatters) -> topic title shown in Telegram
TOPIC_TITLES: dict[str, str] = {
    "Speeding": "Speeding",
    "Crash": "Crash",
    "HarshBraking": "Harsh Braking",
    "HarshTurn": "Harsh Turn",
    "HarshAccel": "Harsh Acceleration",
    "BatteryVoltage": "Battery Voltage",
    "FaultCodes": "Fault Codes",
    "ObstructedCamera": "Obstructed Camera",
    "Drowsy": "Drowsy",
    "FollowingDistance": "Following Distance",
    "MobileUsage": "Mobile Usage",
    "NoSeatbelt": "No Seatbelt",
    "RollingStop": "Rolling Stop",
    "CollisionWarning": "Collision Warning",
}

_RETRY_AFTER_FAILURE_SECONDS = 300


class TopicManager:
    """Maps alert types to Telegram forum topics, creating each topic on first use."""

    def __init__(self, settings: Settings, telegram: TelegramClient) -> None:
        self.settings = settings
        self.telegram = telegram
        self._map: dict[str, int] = {}
        self._lock = asyncio.Lock()
        self._failed_until: dict[str, float] = {}
        self._file = Path(settings.topics_file)
        self._map.update(self._read_file())
        self._map.update(settings.topic_ids())  # env var wins over the local file
        self._map.pop("General", None)  # General = the group's built-in topic (no thread id)

    def _read_file(self) -> dict[str, int]:
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
            return {str(k): int(v) for k, v in data.items()}
        except (OSError, ValueError, AttributeError):
            return {}

    def _save(self) -> None:
        try:
            self._file.write_text(json.dumps(self._map), encoding="utf-8")
        except OSError:
            pass

    def snapshot(self) -> dict[str, int]:
        return dict(self._map)

    def forget(self, key: str) -> None:
        self._map.pop(key, None)
        self._save()

    async def thread_for(self, key: str) -> int | None:
        """Thread id for this alert type; None means the group's General topic."""
        if key not in TOPIC_TITLES:
            return None
        if key in self._map:
            return self._map[key]
        async with self._lock:
            if key in self._map:
                return self._map[key]
            if time.monotonic() < self._failed_until.get(key, 0):
                return None
            return await self._create(key)

    async def _create(self, key: str) -> int | None:
        result = await self.telegram.create_forum_topic(TOPIC_TITLES[key])
        if not result.get("ok"):
            logger.error(
                "Could not create topic %r. Make sure Topics is ON for the group and the bot is "
                "admin with 'Manage Topics'. Telegram said: %s",
                TOPIC_TITLES[key],
                result.get("description"),
            )
            self._failed_until[key] = time.monotonic() + _RETRY_AFTER_FAILURE_SECONDS
            return None
        thread_id = int(result["result"]["message_thread_id"])
        self._map[key] = thread_id
        self._save()
        # Copy this line into the TOPIC_MAP env var so restarts don't create duplicates.
        logger.warning("Created topic %r -> %s | TOPIC_MAP=%s", TOPIC_TITLES[key], thread_id, json.dumps(self._map))
        return thread_id

    async def ensure_all(self) -> dict[str, int]:
        for key in TOPIC_TITLES:
            created_before = len(self._map)
            await self.thread_for(key)
            if len(self._map) != created_before:
                await asyncio.sleep(0.5)  # be gentle with Telegram
        return self.snapshot()
