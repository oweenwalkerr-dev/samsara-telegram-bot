from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timezone, tzinfo
from typing import Any

logger = logging.getLogger(__name__)

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

# Nicer display titles for labels whose raw name is vague
_TITLE_OVERRIDES = {"braking": "Harsh Braking", "acceleration": "Harsh Acceleration"}


def _compact(raw: str) -> str:
    return raw.lower().replace(" ", "").replace("_", "")


def pretty_title(raw: str) -> str:
    """'FollowingDistance' -> 'Following Distance', 'Braking' -> 'Harsh Braking'."""
    override = _TITLE_OVERRIDES.get(_compact(raw))
    if override:
        return override
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", raw.replace("_", " ")).strip()
    spaced = spaced or raw
    return spaced[:1].upper() + spaced[1:]


def topic_key_from_labels(labels: list[str] | None) -> str:
    if not labels:
        return "General"
    for raw in labels:
        key = LABEL_TO_TOPIC_KEY.get(_compact(raw)) or LABEL_TO_TOPIC_KEY.get(raw.lower())
        if key:
            return key
    logger.info("Unmapped safety label(s) %s -> General (add to LABEL_TO_TOPIC_KEY)", labels)
    return "General"


def topic_key_from_alert_condition(condition_id: str | None) -> str:
    if not condition_id:
        return "General"
    return LABEL_TO_TOPIC_KEY.get(_compact(condition_id), "General")


def _esc(value: Any) -> str:
    if value is None or value == "":
        return "—"
    return html.escape(str(value))


def _to_dt(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        ts = value / 1000 if value > 10_000_000_000 else value
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _fmt_time(value: Any, tz: tzinfo = timezone.utc) -> str:
    if not value:
        return "—"
    dt = _to_dt(value)
    if dt is None:
        return str(value)
    local = dt.astimezone(tz)
    clock = local.strftime("%I:%M %p").lstrip("0")
    return f"{local.strftime('%b')} {local.day}, {clock} {local.strftime('%Z')}"


def _location_line(location: Any) -> str:
    if not isinstance(location, dict):
        return "<b>Location:</b> —"
    reverse = location.get("reverseGeo")
    address = reverse.get("formattedLocation") if isinstance(reverse, dict) else None
    lat, lon = location.get("latitude"), location.get("longitude")
    link = f'<a href="https://maps.google.com/?q={lat},{lon}">Open map</a>' if lat is not None and lon is not None else ""
    if address and link:
        return f"<b>Location:</b> {_esc(address)} ({link})"
    if address:
        return f"<b>Location:</b> {_esc(address)}"
    if link:
        return f"<b>Location:</b> {link}"
    return "<b>Location:</b> —"


def format_safety_event(
    event: dict[str, Any],
    *,
    vehicle_name: str | None = None,
    driver_name: str | None = None,
    tz: tzinfo = timezone.utc,
) -> tuple[str, str]:
    """Return (topic_key, html_caption)."""
    labels = []
    for item in event.get("behaviorLabels") or []:
        if isinstance(item, dict):
            labels.append(item.get("label") or item.get("name") or "")
        else:
            labels.append(str(item))
    labels = [x for x in labels if x]

    topic = topic_key_from_labels(labels)
    title = pretty_title(labels[0]) if labels else str(event.get("type") or "Safety Event")

    time_text = _fmt_time(event.get("time") or event.get("createdAtTime") or event.get("startTime"), tz)

    lines = [
        f"<b>🚨 {_esc(title)}</b>",
        f"<b>Vehicle:</b> {_esc(vehicle_name)}",
        f"<b>Driver:</b> {_esc(driver_name)}",
        f"<b>Time:</b> {_esc(time_text)}",
        _location_line(event.get("location")),
    ]

    mph = event.get("speedMilesPerHour")
    if mph is None and event.get("vehicleSpeedMetersPerSecond") is not None:
        try:
            mph = float(event["vehicleSpeedMetersPerSecond"]) * 2.23694
        except (TypeError, ValueError):
            mph = None
    if mph is not None:
        try:
            lines.append(f"<b>Speed:</b> {float(mph):.0f} mph")
        except (TypeError, ValueError):
            lines.append(f"<b>Speed:</b> {_esc(mph)}")

    if event.get("id"):
        lines.append(f"<i>Event ID: {_esc(event.get('id'))}</i>")

    return topic, "\n".join(lines)


def format_webhook_alert(payload: dict[str, Any], tz: tzinfo = timezone.utc) -> tuple[str, str]:
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
        f"<b>⚡ {_esc(pretty_title(str(condition)))}</b>",
        f"{_esc(summary)}",
        f"<b>Vehicle:</b> {_esc(device.get('name') if isinstance(device, dict) else device)}",
        f"<b>Driver:</b> {_esc(driver.get('name') if isinstance(driver, dict) else driver)}",
        f"<b>Time:</b> {_esc(_fmt_time(event.get('startMs') or event.get('eventMs') or payload.get('eventTime'), tz))}",
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
