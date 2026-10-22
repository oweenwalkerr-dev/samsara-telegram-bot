from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

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
    topic_map: str = "{}"

    safety_poll_seconds: int = 45
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
