from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, Response

from app.config import get_settings
from app.formatters import extract_people, format_webhook_alert
from app.publisher import Publisher
from app.safety_poller import SafetyPoller
from app.samsara_client import SamsaraClient
from app.telegram_client import TelegramClient
from app.topics import TopicManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("app")
# httpx logs full request URLs at INFO, which would put the Telegram bot token in the logs.
logging.getLogger("httpx").setLevel(logging.WARNING)

settings = get_settings()
samsara: SamsaraClient | None = None
telegram: TelegramClient | None = None
poller: SafetyPoller | None = None
topics: TopicManager | None = None
publisher: Publisher | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global samsara, telegram, poller, topics, publisher
    samsara = SamsaraClient(settings)
    telegram = TelegramClient(settings)
    topics = TopicManager(settings, telegram)
    publisher = Publisher(telegram, topics)
    poller = SafetyPoller(settings, samsara, publisher)
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


@app.api_route("/health", methods=["GET", "HEAD"])
@app.api_route("/", methods=["GET", "HEAD"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/webhook/samsara")
async def samsara_webhook(
    request: Request,
    x_samsara_timestamp: str | None = Header(default=None),
    x_samsara_signature: str | None = Header(default=None),
    x_samsara_event_type: str | None = Header(default=None),
) -> Response:
    assert samsara is not None and publisher is not None

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

    # Log what Samsara sent (helps when an alert type needs tuning)
    logger.info("Webhook payload (%s): %s", event_type, json.dumps(payload)[:2000])

    try:
        vehicle, driver = extract_people(payload)
        vehicle_name = driver_name = None
        if vehicle or driver:
            vehicle_name, driver_name = await samsara.resolve_event_people(
                {"asset": vehicle or {}, "driver": driver or {}}
            )
        topic_key, text = format_webhook_alert(
            payload, tz=settings.display_tz(), vehicle_name=vehicle_name, driver_name=driver_name
        )
    except Exception:
        logger.exception("Could not format webhook payload")
        topic_key, text = "General", "⚡ Samsara alert received (could not format it; see logs)"

    ok = await publisher.publish(topic_key, text)
    if not ok:
        logger.error("Failed to send telegram message for webhook event %s", event_type)
        # Still 200 so Samsara does not retry forever on formatting bugs
    return Response(status_code=200)


@app.post("/test/telegram")
async def test_telegram(message: str = "Samsara bot is online ✅") -> dict[str, Any]:
    """Manual smoke test: POST /test/telegram"""
    assert telegram is not None
    result = await telegram.send_message(message)
    return result


@app.api_route("/setup/topics", methods=["GET", "POST"])
async def setup_topics() -> dict[str, Any]:
    """Create every alert topic that does not exist yet.

    Returns the TOPIC_MAP value to paste into your host's environment variables
    (so restarts and redeploys don't create duplicate topics).
    """
    assert topics is not None
    mapping = await topics.ensure_all()
    return {"TOPIC_MAP": json.dumps(mapping), "topics_created_or_known": len(mapping)}
