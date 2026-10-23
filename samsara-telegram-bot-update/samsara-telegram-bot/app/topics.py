"""Runtime topic routing. Env TOPIC_MAP is the base; /sync-topics can add more."""

from __future__ import annotations

RUNTIME_TOPICS: dict[str, int] = {}

# Telegram topic title or the word they type -> key used by the poller
_ALIASES = {
    "general": "General",
    "speeding": "Speeding",
    "severe speeding": "Speeding",
    "severespeeding": "Speeding",
    "crash": "Crash",
    "harsh breaking": "HarshBraking",
    "harsh braking": "HarshBraking",
    "braking": "HarshBraking",
    "obstructed camera": "ObstructedCamera",
    "obstructedcamera": "ObstructedCamera",
    "battery voltage": "BatteryVoltage",
    "battery": "BatteryVoltage",
    "fault codes": "FaultCodes",
    "fault code": "FaultCodes",
    "faultcodes": "FaultCodes",
    "following distance": "FollowingDistance",
    "followingdistance": "FollowingDistance",
    "drowsy": "Drowsy",
    "harsh turn": "HarshTurn",
    "mobile usage": "MobileUsage",
    "no seatbelt": "NoSeatbelt",
    "rolling stop": "RollingStop",
}


def canonical_topic(name: str) -> str | None:
    cleaned = (name or "").strip().lower()
    cleaned = cleaned.replace("@alerts_samsara_bot", "").strip(" .,:;/-")
    if not cleaned:
        return None
    if cleaned in _ALIASES:
        return _ALIASES[cleaned]
    squashed = cleaned.replace(" ", "").replace("_", "")
    for alias, key in _ALIASES.items():
        if alias.replace(" ", "") == squashed:
            return key
    return None


def effective_topic_ids(settings_map: dict[str, int]) -> dict[str, int]:
    merged = dict(settings_map)
    merged.update(RUNTIME_TOPICS)
    return merged


def remember_from_updates(updates: list[dict]) -> dict[str, int]:
    found: dict[str, int] = {}
    for update in updates:
        message = update.get("message") or update.get("channel_post") or {}
        if not isinstance(message, dict):
            continue
        thread_id = message.get("message_thread_id")
        created = message.get("forum_topic_created") or {}
        reply = message.get("reply_to_message") or {}
        if not created and isinstance(reply, dict):
            created = reply.get("forum_topic_created") or {}
        name = ""
        if isinstance(created, dict):
            name = str(created.get("name") or "")
        if not name:
            name = str(message.get("text") or message.get("caption") or "")
        key = canonical_topic(name)
        if key and thread_id:
            try:
                found[key] = int(thread_id)
            except (TypeError, ValueError):
                continue
    RUNTIME_TOPICS.update(found)
    return dict(RUNTIME_TOPICS)
