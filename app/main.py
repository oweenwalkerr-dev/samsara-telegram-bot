from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, Response

from app.config import get_settings
from app.formatters import format_webhook_alert
from app.safety_poller import SafetyPoller
from app.samsara_client import SamsaraClient
from app.telegram_client import TelegramClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("app")

settings = get_settings()
samsara: SamsaraClient | None = None
telegram: TelegramClient | None = None
poller: SafetyPoller | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global samsara, telegram, poller
    samsara = SamsaraClient(settings)
    telegram = TelegramClient(settings)
    poller = SafetyPoller(settings, samsara, telegram)
    poller.start()
    logger.info("App started. Chat ID=%s", settings.telegram_chat_id)
    yield
    if poller:
        await poller.stop()
    if samsara:
        await samsara.aclose()
    if telegram:
        await telegram.aclose()


app = FastAPI(title="Samsara → Telegram Bot", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/webhook/samsara")
async def samsara_webhook(
    request: Request,
    x_samsara_timestamp: str | None = Header(default=None),
    x_samsara_signature: str | None = Header(default=None),
    x_samsara_event_type: str | None = Header(default=None),
) -> Response:
    assert samsara is not None and telegram is not None

    body = await request.body()

    # Ping / test events from Samsara dashboard
    if x_samsara_event_type == "Ping":
        return Response(status_code=200)

    if x_samsara_timestamp and x_samsara_signature:
        ok = samsara.verify_webhook_signature(
            timestamp=x_samsara_timestamp,
            body=body,
            signature_header=x_samsara_signature,
        )
        if not ok:
            raise HTTPException(status_code=401, detail="invalid signature")

    try:
        payload: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json")

    event_type = payload.get("eventType") or x_samsara_event_type or ""
    if event_type == "Ping":
        return Response(status_code=200)

    topic_key, text = format_webhook_alert(payload)
    topics = settings.topic_ids()
    thread_id = topics.get(topic_key) or topics.get("General")

    result = await telegram.send_message(text, thread_id=thread_id)
    if not result.get("ok"):
        logger.error("Failed to send telegram message: %s", result)
        # Still 200 so Samsara does not retry forever on formatting bugs
    return Response(status_code=200)


@app.post("/test/telegram")
async def test_telegram(message: str = "Samsara bot is online ✅") -> dict[str, Any]:
    """Manual smoke test: POST /test/telegram"""
    assert telegram is not None
    topics = settings.topic_ids()
    thread_id = topics.get("General")
    result = await telegram.send_message(message, thread_id=thread_id)
    return result
