"""Derive HA automation signals from verified portal observations."""

import math
from asyncio import Lock
from collections import defaultdict, deque
from datetime import timedelta

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.recorder import get_instance
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .usage import SHANGHAI

SNAPSHOT_KEYS = ("data_used", "monthly_spend", "voice_remaining")
SPIKE_THRESHOLD = 5.0


def snapshot_store(hass, entry_id):
    """Keep only account metrics, never credentials, in per-entry storage."""
    return Store(hass, 1, f"{DOMAIN}.{entry_id}.usage", private=True)


def package_changes(previous, current):
    """Describe additions, removals and one-to-one changes in validity."""

    def group(packages):
        grouped = defaultdict(list)
        for package in packages.values():
            grouped[package["category"], package["name"]].append(
                (package["start"].isoformat(), package["end"].isoformat())
            )
        return grouped

    before, after = group(previous), group(current)
    changes = []
    for category, name in sorted(before.keys() | after.keys()):
        old = set(before[category, name])
        new = set(after[category, name])
        if old == new:
            continue
        common = old & new
        old, new = old - common, new - common
        if len(old) == len(new) == 1:
            removed, added = next(iter(old)), next(iter(new))
            kind = "renewed" if added[1] > removed[1] else "validity_changed"
            changes.append(
                {
                    "type": kind,
                    "category": category,
                    "name": name,
                    "valid_from": added[0],
                    "valid_until": added[1],
                    "previous_valid_until": removed[1],
                }
            )
            continue
        for kind, windows in (("removed", old), ("added", new)):
            changes.extend(
                {
                    "type": kind,
                    "category": category,
                    "name": name,
                    "valid_from": start,
                    "valid_until": end,
                }
                for start, end in sorted(windows)
            )
    return changes


def daily_rate(history, now, used, scope):
    """Estimate recent use only while the selected allowance stays comparable."""
    previous = used
    baseline = None
    for state in reversed(history):
        if (
            state.attributes.get("usage_scope"),
            state.attributes.get("unit_of_measurement"),
        ) != (scope, "GB"):
            break
        try:
            value = float(state.state)
        except TypeError, ValueError:
            break
        if not math.isfinite(value) or not 0 <= value <= previous:
            break
        baseline = state.last_updated, value
        previous = value
    if baseline is None:
        return None
    time, value = baseline
    days = (now - time).total_seconds() / 86400
    return (used - value) / days if days >= 1 and used > value else None


def _recorded_rate(hass, entity_id, now, used, scope):
    from sqlalchemy.exc import SQLAlchemyError

    from homeassistant.components.recorder.history import get_significant_states

    try:
        states = get_significant_states(
            hass,
            now - timedelta(days=7),
            now,
            [entity_id],
            include_start_time_state=False,
        )
        return daily_rate(states.get(entity_id, ()), now, used, scope)
    except SQLAlchemyError:
        return None


class UsageInsights:
    """Maintain a short spike window and archive the latest observations per month."""

    def __init__(self, hass, entry_id):
        self.hass = hass
        self.entry_id = entry_id
        self.store = snapshot_store(hass, entry_id)
        self.history = None
        self._archive_lock = Lock()
        self._packages = None
        self._scope = None
        self._samples = deque()
        self._spike_active = False
        self._rate = None
        self._rate_at = None

    async def async_apply(self, data, now):
        """Attach derived readings and publish changes after successful queries."""
        if data["resources_ok"]:
            packages = data["packages"]
            if self._packages is not None:
                changes = package_changes(self._packages, packages)
                if changes:
                    self.hass.bus.async_fire(
                        f"{DOMAIN}_packages_changed",
                        {"entry_id": self.entry_id, "changes": changes},
                    )
            self._packages = packages

        used = data.get("data_used")
        scope = data.get("usage_scope")
        if (
            scope != self._scope
            or used is None
            or (self._samples and used < self._samples[-1][1])
        ):
            self._scope = scope
            self._samples.clear()
            self._spike_active = False
            self._rate = self._rate_at = None
        if used is not None and scope is not None:
            self._observe_spike(now, used)
            await self._async_estimate(data, now, used, scope)

        data["monthly_snapshots"] = await self.async_archive(now, data)

    async def async_archive(self, now, data=None):
        """Close the previous Beijing month even while the portal is offline."""
        async with self._archive_lock:
            if self.history is None:
                self.history = await self.store.async_load() or {
                    "current": {},
                    "months": [],
                }
            month = now.astimezone(SHANGHAI).strftime("%Y-%m")
            current = self.history["current"]
            changed = False
            if current.get("month", "") < month:
                if current.get("readings"):
                    self.history["months"] = [current, *self.history["months"]][:12]
                current = self.history["current"] = {"month": month, "readings": {}}
                changed = True
            if data is not None and current["month"] == month:
                for key in SNAPSHOT_KEYS:
                    if data.get(key) is not None:
                        current["readings"][key] = {
                            "value": data[key],
                            "observed_at": now.isoformat(),
                        }
                        changed = True
            if changed:
                await self.store.async_save(self.history)
            return self.history["months"]

    def _observe_spike(self, now, used):
        cutoff = now - timedelta(hours=1)
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()
        self._samples.append((now, used))
        baseline, prior = self._samples[0]
        delta = used - prior
        if delta >= SPIKE_THRESHOLD and not self._spike_active:
            self.hass.bus.async_fire(
                f"{DOMAIN}_usage_spike",
                {
                    "entry_id": self.entry_id,
                    "used_gb": round(delta, 3),
                    "window_seconds": int((now - baseline).total_seconds()),
                },
            )
        self._spike_active = delta >= SPIKE_THRESHOLD

    async def _async_estimate(self, data, now, used, scope):
        if "recorder" not in self.hass.config.components:
            return
        if self._rate_at is None or now - self._rate_at >= timedelta(hours=1):
            entity_id = er.async_get(self.hass).async_get_entity_id(
                "sensor", DOMAIN, f"{self.entry_id}_data_used"
            )
            if entity_id is None:
                return
            self._rate_at = now
            self._rate = await get_instance(self.hass).async_add_executor_job(
                _recorded_rate, self.hass, entity_id, now, used, scope
            )
        if self._rate:
            data["remaining_days"] = round(data["data_remaining"] / self._rate, 2)
