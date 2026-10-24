from __future__ import annotations

import json
from datetime import timezone, tzinfo
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    samsara_api_token: str = ""
    samsara_webhook_secret: str = ""
    samsara_base_url: str = "https://api.samsara.com"

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # JSON object: {"Speeding": 123, "Crash": 456, ...}
    # Topics are now created automatically; paste the map the app logs / returns
    # from /setup/topics here so it survives restarts and redeploys.
    topic_map: str = "{}"
    # Best-effort local copy of the topic map (lost on redeploy on most hosts).
    topics_file: str = "topics.json"

    # Timezone used for the "Time:" line in alerts
    display_timezone: str = "America/Chicago"

    safety_poll_seconds: int = 45

    # Fault codes + battery voltage (read directly from Samsara vehicle stats)
    stats_poll_seconds: int = 60
    fault_alerts_enabled: bool = True
    fault_cooldown_hours: float = 12  # don't repeat the same code on the same truck within this time
    battery_alerts_enabled: bool = True
    battery_low_volts: float = 11.8
    battery_high_volts: float = 15.0
    battery_min_readings: int = 2  # consecutive readings beyond the limit (readings come ~every 2 min)
    battery_cooldown_hours: float = 6
    port: int = 8080

    def topic_ids(self) -> dict[str, int]:
        try:
            data: Any = json.loads(self.topic_map or "{}")
        except json.JSONDecodeError:
            return {}
        out: dict[str, int] = {}
        if isinstance(data, dict):
            for key, value in data.items():
                try:
                    out[str(key)] = int(value)
                except (TypeError, ValueError):
                    continue
        return out

    def display_tz(self) -> tzinfo:
        try:
            return ZoneInfo(self.display_timezone)
        except Exception:
            return timezone.utc


@lru_cache
def get_settings() -> Settings:
    return Settings()
