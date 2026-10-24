from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from app.config import Settings
from app.formatters import _esc, _fmt_time, _location_line
from app.publisher import Publisher
from app.samsara_client import SamsaraClient

logger = logging.getLogger(__name__)

MAX_POSTS_PER_CYCLE = 15  # safety valve: never flood the group


# --------------------------------------------------------------------------- fault parsing
def parse_fault_reading(reading: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    """Return ({code_key: readable text}, [lamps on]) for one faultCodes reading (J1939 or OBD-II)."""
    codes: dict[str, str] = {}
    lamps: list[str] = []

    j1939 = reading.get("j1939")
    if isinstance(j1939, dict):
        lights = j1939.get("checkEngineLights") or {}
        for key, label in (
            ("stopIsOn", "Stop"),
            ("warningIsOn", "Warning"),
            ("protectIsOn", "Protect"),
            ("emissionsIsOn", "Emissions"),
        ):
            if lights.get(key):
                lamps.append(label)
        for dtc in j1939.get("diagnosticTroubleCodes") or []:
            if not isinstance(dtc, dict):
                continue
            spn, fmi = dtc.get("spnId"), dtc.get("fmiId")
            key = f"j1939:{dtc.get('txId')}:{spn}:{fmi}"
            text = f"SPN {spn} / FMI {fmi}"
            desc = " - ".join(str(x) for x in (dtc.get("spnDescription"), dtc.get("fmiDescription")) if x)
            if desc:
                text += f": {desc}"
            extras = []
            if dtc.get("sourceAddressName"):
                extras.append(str(dtc["sourceAddressName"]))
            if dtc.get("occurrenceCount"):
                extras.append(f"x{dtc['occurrenceCount']}")
            if extras:
                text += f" ({', '.join(extras)})"
            codes[key] = text

    obdii = reading.get("obdii")
    if isinstance(obdii, dict):
        if obdii.get("checkEngineLightIsOn"):
            lamps.append("Check engine")
        for module in obdii.get("diagnosticTroubleCodes") or []:
            if not isinstance(module, dict):
                continue
            for kind in ("confirmedDtcs", "permanentDtcs"):  # skip "pending": too noisy
                for dtc in module.get(kind) or []:
                    if not isinstance(dtc, dict):
                        continue
                    code = dtc.get("dtcShortCode") or dtc.get("dtcId")
                    if code is None:
                        continue
                    text = f"{code}"
                    if dtc.get("dtcDescription"):
                        text += f": {dtc['dtcDescription']}"
                    codes[f"obdii:{code}"] = text

    return codes, list(dict.fromkeys(lamps))


def format_fault_message(
    vehicle: str | None, when: Any, new_codes: list[str], lamps: list[str], gps: Any, tz: Any
) -> str:
    icon = "🛑" if "Stop" in lamps else "🔧"
    lines = [
        f"<b>{icon} Fault Code</b>",
        f"<b>Vehicle:</b> {_esc(vehicle)}",
        f"<b>Time:</b> {_esc(_fmt_time(when, tz))}",
    ]
    if isinstance(gps, dict) and gps:
        lines.append(_location_line(gps))
    if lamps:
        lines.append(f"<b>Lamps on:</b> {_esc(', '.join(lamps))}")
    for text in new_codes[:6]:
        lines.append(f"• {_esc(text)}")
    if len(new_codes) > 6:
        lines.append(f"…and {len(new_codes) - 6} more")
    return "\n".join(lines)


def format_battery_message(vehicle: str | None, when: Any, volts: float, kind: str, limit: float, tz: Any) -> str:
    title = "Low Battery Voltage" if kind == "low" else "High Battery Voltage"
    compare = "below" if kind == "low" else "above"
    return "\n".join(
        [
            f"<b>🔋 {title}</b>",
            f"<b>Vehicle:</b> {_esc(vehicle)}",
            f"<b>Voltage:</b> {volts:.1f} V (alert {compare} {limit:g} V)",
            f"<b>Time:</b> {_esc(_fmt_time(when, tz))}",
        ]
    )


# --------------------------------------------------------------------------- poller
class StatsPoller:
    """Follows Samsara's vehicle stats feeds for fault codes and battery voltage."""

    def __init__(self, settings: Settings, samsara: SamsaraClient, publisher: Publisher) -> None:
        self.settings = settings
        self.samsara = samsara
        self.publisher = publisher
        self.tz = settings.display_tz()
        self._cursors: dict[str, str | None] = {}
        self._baselined: set[str] = set()
        self._sample_logged: set[str] = set()
        # fault state
        self._active: dict[str, set[str]] = {}
        self._fault_last_post: dict[tuple[str, str], float] = {}
        # battery state
        self._streak: dict[tuple[str, str], int] = {}
        self._battery_last_post: dict[tuple[str, str], float] = {}
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if not (self.settings.fault_alerts_enabled or self.settings.battery_alerts_enabled):
            return
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="stats-poller")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except Exception:
                self._task.cancel()

    async def _run(self) -> None:
        logger.info("Stats poller started (every %ss)", self.settings.stats_poll_seconds)
        while not self._stop.is_set():
            if self.settings.samsara_api_token:
                if self.settings.fault_alerts_enabled:
                    await self._safe(self.poll_faults)
                if self.settings.battery_alerts_enabled:
                    await self._safe(self.poll_battery)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.settings.stats_poll_seconds)
            except asyncio.TimeoutError:
                pass

    @staticmethod
    async def _safe(fn: Any) -> None:
        try:
            await fn()
        except Exception:
            logger.exception("Stats poll failed (%s)", getattr(fn, "__name__", fn))

    async def _fetch_all(self, types: str, decorations: str | None = None) -> tuple[list[dict[str, Any]], bool]:
        """Read every page of the feed. Returns (rows, is_baseline)."""
        baseline = types not in self._baselined
        cursor = self._cursors.get(types)
        rows: list[dict[str, Any]] = []
        for _ in range(60):
            payload = await self.samsara.get_stats_feed(types=types, after=cursor, decorations=decorations)
            rows.extend(r for r in (payload.get("data") or []) if isinstance(r, dict))
            page = payload.get("pagination") or {}
            if page.get("endCursor"):
                cursor = page["endCursor"]
            if not page.get("hasNextPage"):
                break
        self._cursors[types] = cursor
        self._baselined.add(types)
        return rows, baseline

    def _log_sample(self, types: str, rows: list[dict[str, Any]]) -> None:
        if types in self._sample_logged:
            return
        for row in rows:
            if row.get(types):
                logger.info("Sample %s row: %s", types, json.dumps(row)[:900])
                self._sample_logged.add(types)
                return

    async def _vehicle_name(self, vehicle_id: str, name: Any) -> str | None:
        resolved, _ = await self.samsara.resolve_event_people({"asset": {"id": vehicle_id, "name": name}, "driver": {}})
        return resolved

    # ------------------------------------------------------------------ faults
    async def poll_faults(self) -> int:
        rows, baseline = await self._fetch_all("faultCodes", decorations="gps")
        self._log_sample("faultCodes", rows)
        cooldown = self.settings.fault_cooldown_hours * 3600
        posted = 0
        suppressed = 0

        for row in rows:
            vid = str(row.get("id") or "")
            if not vid:
                continue
            readings = [r for r in (row.get("faultCodes") or []) if isinstance(r, dict)]
            readings.sort(key=lambda r: str(r.get("time") or ""))
            for reading in readings:
                codes, lamps = parse_fault_reading(reading)
                previous = self._active.get(vid, set())
                self._active[vid] = set(codes)
                if baseline:
                    continue
                now = time.time()
                fresh = [
                    k for k in codes
                    if k not in previous and now - self._fault_last_post.get((vid, k), 0.0) >= cooldown
                ]
                if not fresh:
                    continue
                if posted >= MAX_POSTS_PER_CYCLE:
                    suppressed += 1
                    continue
                gps = (reading.get("decorations") or {}).get("gps")
                text = format_fault_message(
                    await self._vehicle_name(vid, row.get("name")),
                    reading.get("time"),
                    [codes[k] for k in fresh],
                    lamps,
                    gps,
                    self.tz,
                )
                ok = await self.publisher.publish("FaultCodes", text)
                logger.info("Fault alert vehicle=%s codes=%s ok=%s", vid, fresh, ok)
                for k in fresh:
                    self._fault_last_post[(vid, k)] = now
                posted += 1
                await asyncio.sleep(1.2)

        if baseline:
            logger.info("Fault feed baseline set for %d vehicles", len(self._active))
        if suppressed:
            logger.warning("Suppressed %d fault alerts (per-cycle limit)", suppressed)
        return posted

    # ------------------------------------------------------------------ battery
    async def poll_battery(self) -> int:
        rows, baseline = await self._fetch_all("batteryMilliVolts")
        self._log_sample("batteryMilliVolts", rows)
        s = self.settings
        cooldown = s.battery_cooldown_hours * 3600
        posted = 0

        for row in rows:
            vid = str(row.get("id") or "")
            if not vid:
                continue
            readings = [r for r in (row.get("batteryMilliVolts") or []) if isinstance(r, dict)]
            readings.sort(key=lambda r: str(r.get("time") or ""))
            for reading in readings:
                try:
                    volts = float(reading.get("value")) / 1000.0
                except (TypeError, ValueError):
                    continue
                for kind, flagged, limit in (
                    ("low", volts < s.battery_low_volts, s.battery_low_volts),
                    ("high", volts > s.battery_high_volts, s.battery_high_volts),
                ):
                    key = (vid, kind)
                    self._streak[key] = self._streak.get(key, 0) + 1 if flagged else 0
                    if baseline or not flagged or self._streak[key] < s.battery_min_readings:
                        continue
                    now = time.time()
                    if now - self._battery_last_post.get(key, 0.0) < cooldown or posted >= MAX_POSTS_PER_CYCLE:
                        continue
                    text = format_battery_message(
                        await self._vehicle_name(vid, row.get("name")), reading.get("time"), volts, kind, limit, self.tz
                    )
                    ok = await self.publisher.publish("BatteryVoltage", text)
                    logger.info("Battery alert vehicle=%s %s %.2fV ok=%s", vid, kind, volts, ok)
                    self._battery_last_post[key] = now
                    posted += 1
                    await asyncio.sleep(1.2)

        if baseline:
            logger.info("Battery feed baseline set")
        return posted
