from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


class TelegramClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base = f"https://api.telegram.org/bot{settings.telegram_bot_token}"
        self._client = httpx.AsyncClient(timeout=60.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, method: str, data: dict[str, Any] | None = None, files: Any = None) -> dict[str, Any]:
        url = f"{self.base}/{method}"
        if files:
            resp = await self._client.post(url, data=data or {}, files=files)
        else:
            resp = await self._client.post(url, json=data or {})
        try:
            payload = resp.json()
        except Exception:
            payload = {"ok": False, "description": resp.text}
        if not payload.get("ok"):
            logger.error("Telegram %s failed: %s", method, payload)
        return payload

    def _thread_kwargs(self, thread_id: int | None) -> dict[str, Any]:
        if thread_id is None:
            return {}
        return {"message_thread_id": thread_id}

    async def send_message(
        self,
        text: str,
        *,
        thread_id: int | None = None,
        parse_mode: str = "HTML",
        disable_preview: bool = True,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "chat_id": self.settings.telegram_chat_id,
            "text": text[:4096],
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_preview,
            **self._thread_kwargs(thread_id),
        }
        return await self._post("sendMessage", data=data)

    async def send_video(
        self,
        video_bytes: bytes,
        caption: str,
        *,
        thread_id: int | None = None,
        filename: str = "dashcam.mp4",
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "chat_id": self.settings.telegram_chat_id,
            "caption": caption[:1024],
            "parse_mode": "HTML",
            "supports_streaming": "true",
            **{k: str(v) for k, v in self._thread_kwargs(thread_id).items()},
        }
        files = {"video": (filename, video_bytes, "video/mp4")}
        return await self._post("sendVideo", data=data, files=files)

    async def send_photo(
        self,
        photo_bytes: bytes,
        caption: str = "",
        *,
        thread_id: int | None = None,
        filename: str = "report.png",
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "chat_id": self.settings.telegram_chat_id,
            "caption": caption[:1024],
            "parse_mode": "HTML",
            **{k: str(v) for k, v in self._thread_kwargs(thread_id).items()},
        }
        files = {"photo": (filename, photo_bytes, "image/png")}
        return await self._post("sendPhoto", data=data, files=files)

    async def pin_message(self, message_id: int) -> dict[str, Any]:
        return await self._post(
            "pinChatMessage",
            data={
                "chat_id": self.settings.telegram_chat_id,
                "message_id": message_id,
                "disable_notification": True,
            },
        )
