from __future__ import annotations

import html
from datetime import datetime
from typing import Any


# Canonical keys used in TOPIC_MAP env
LABEL_TO_TOPIC_KEY: dict[str, str] = {
    # Safety behavior labels (Samsara)
    "harshturn": "HarshTurn",
    "harsh turn": "HarshTurn",
    "braking": "HarshBraking",
    "harshbrake": "HarshBraking",
    "harsh braking": "HarshBraking",
    "harsh breaking": "HarshBraking",
    "acceleration": "HarshAccel",
    "crash": "Crash",
    "drowsy": "Drowsy",
    "followingdistance": "FollowingDistance",
    "followingdistancemoderate": "FollowingDistance",
    "followingdistancesevere": "FollowingDistance",
    "mobileusage": "MobileUsage",
    "noseatbelt": "NoSeatbelt",
    "obstructedcamera": "ObstructedCamera",
    "rollingstop": "RollingStop",
    "speeding": "Speeding",
    "severespeeding": "Speeding",
    "heavyspeeding": "Speeding",
    "moderatespeeding": "Speeding",
    "lightspeeding": "Speeding",
    "forwardcollisionwarning": "CollisionWarning",
    # Alert condition ids
    "devicehasvehiclefault": "FaultCodes",
    "deviceunplugged": "BatteryVoltage",
    "devicespeedabovespeedlimit": "Speeding",
    "dashcamdisconnected": "ObstructedCamera",
}


def topic_key_from_labels(labels: list[str] | None) -> str:
    if not labels:
        return "General"
    for raw in labels:
        key = LABEL_TO_TOPIC_KEY.get(raw.lower().replace(" ", "").replace("_", ""))
        if key:
            return key
        key = LABEL_TO_TOPIC_KEY.get(raw.lower())
        if key:
            return key
    return "General"


def topic_key_from_alert_condition(condition_id: str | None) -> str:
    if not condition_id:
        return "General"
    normalized = condition_id.lower().replace(" ", "").replace("_", "")
    return LABEL_TO_TOPIC_KEY.get(normalized, "General")


def _esc(value: Any) -> str:
    if value is None:
        return "—"
    return html.escape(str(value))


def _fmt_time(value: Any) -> str:
    if not value:
        return "—"
    if isinstance(value, (int, float)):
        # ms or seconds
        ts = value / 1000 if value > 10_000_000_000 else value
        try:
            return datetime.utcfromtimestamp(ts).strftime("%b %d, %Y %H:%M UTC")
        except Exception:
            return str(value)
    text = str(value)
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y %H:%M %Z")
    except Exception:
        return text


def format_safety_event(event: dict[str, Any]) -> tuple[str, str]:
    """Return (topic_key, html_caption)."""
    labels = []
    for item in event.get("behaviorLabels") or []:
        if isinstance(item, dict):
            labels.append(item.get("label") or item.get("name") or "")
        else:
            labels.append(str(item))
    labels = [x for x in labels if x]

    topic = topic_key_from_labels(labels)
    title = labels[0] if labels else event.get("type") or "Safety Event"

    driver = event.get("driver") or {}
    vehicle = event.get("vehicle") or event.get("asset") or {}
    location = event.get("location") or {}

    driver_name = driver.get("name") if isinstance(driver, dict) else None
    vehicle_name = vehicle.get("name") if isinstance(vehicle, dict) else None
    loc_text = None
    if isinstance(location, dict):
        loc_text = location.get("reverseGeo", {}).get("formattedLocation") if isinstance(
            location.get("reverseGeo"), dict
        ) else None
        if not loc_text and location.get("latitude") is not None:
            loc_text = f"{location.get('latitude')}, {location.get('longitude')}"

    time_text = _fmt_time(event.get("time") or event.get("createdAtTime") or event.get("startTime"))

    lines = [
        f"<b>🚨 {_esc(title)}</b>",
        f"<b>Vehicle:</b> {_esc(vehicle_name)}",
        f"<b>Driver:</b> {_esc(driver_name)}",
        f"<b>Time:</b> {_esc(time_text)}",
        f"<b>Location:</b> {_esc(loc_text)}",
    ]

    speed = event.get("speedMilesPerHour") or event.get("vehicleSpeedMetersPerSecond")
    if speed is not None:
        lines.append(f"<b>Speed:</b> {_esc(speed)}")

    if event.get("id"):
        lines.append(f"<i>Event ID: {_esc(event.get('id'))}</i>")

    return topic, "\n".join(lines)


def format_webhook_alert(payload: dict[str, Any]) -> tuple[str, str]:
    """Handle both Webhooks 1.0 Alert and 2.0 AlertIncident-ish payloads."""
    event = payload.get("event") or payload.get("data") or payload
    if not isinstance(event, dict):
        event = {}

    condition = (
        event.get("alertConditionId")
        or event.get("conditionId")
        or payload.get("eventType")
        or "Alert"
    )
    topic = topic_key_from_alert_condition(str(condition))

    summary = (
        event.get("summary")
        or event.get("details")
        or event.get("alertConditionDescription")
        or payload.get("eventType")
        or "Samsara alert"
    )

    device = event.get("device") or event.get("vehicle") or {}
    driver = event.get("driver") or {}

    lines = [
        f"<b>⚡ {_esc(condition)}</b>",
        f"{_esc(summary)}",
        f"<b>Vehicle:</b> {_esc(device.get('name') if isinstance(device, dict) else device)}",
        f"<b>Driver:</b> {_esc(driver.get('name') if isinstance(driver, dict) else driver)}",
        f"<b>Time:</b> {_esc(_fmt_time(event.get('startMs') or event.get('eventMs') or payload.get('eventTime')))}",
    ]

    url = event.get("alertEventUrl") or event.get("incidentUrl")
    if url:
        lines.append(f'<a href="{html.escape(str(url))}">Open in Samsara</a>')

    return topic, "\n".join(lines)


def extract_media_urls(event: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for key in (
        "downloadForwardVideoUrl",
        "downloadInwardVideoUrl",
        "downloadTrackedInwardVideoUrl",
    ):
        if event.get(key):
            urls.append(str(event[key]))

    media = event.get("media") or event.get("cameraMedia") or []
    if isinstance(media, list):
        for item in media:
            if isinstance(item, dict) and item.get("url"):
                urls.append(str(item["url"]))
            elif isinstance(item, str):
                urls.append(item)
    return urls
