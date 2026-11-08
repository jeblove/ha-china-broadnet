"""Normalize measurements without inferring the scope of a traffic package."""

import re
import unicodedata
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from .const import MAX_AGE, TRAFFIC_KEYS

NUMBER = r"[0-9]{1,12}(?:\.[0-9]{1,9})?"
SHANGHAI = ZoneInfo("Asia/Shanghai")


def amount(text: str, *, signed: bool = False) -> Decimal:
    """Parse a bounded decimal measurement."""
    value = unicodedata.normalize("NFKC", text).strip()
    if not re.fullmatch(("-?" if signed else "") + NUMBER, value):
        raise ValueError("Invalid measurement")
    return Decimal(value)


def traffic_gb(number: str, unit: str, base: int) -> float:
    """Convert SI or IEC input, or a carrier's explicitly selected KB convention."""
    unit = unit.upper()
    binary = "I" in unit or base == 1024
    power = ("B", "KB", "MB", "GB").index(unit.replace("I", ""))
    return float(amount(number) * (1024 if binary else 1000) ** power / 1000**3)


def current_values(reports: dict, now: datetime, max_age: timedelta = MAX_AGE) -> dict:
    """Return values that are still current and the last accepted timestamp."""
    data = {}
    for group, report in reports.items():
        observed = datetime.fromisoformat(report["observed"])
        updated = datetime.fromisoformat(report["updated"])
        data["last_updated"] = max(data.get("last_updated", updated), updated)
        same_month = observed.astimezone(SHANGHAI).date().replace(day=1) == (
            now.astimezone(SHANGHAI).date().replace(day=1)
        )
        if now - observed < max_age and (group != "traffic" or same_month):
            data.update(report["values"])
    return data


def group_for(key: str) -> str:
    """Group measurements sharing the same observation."""
    return "traffic" if key in TRAFFIC_KEYS else "balance"


def make_report(values: dict, observed: datetime, updated: datetime) -> dict:
    """Serialize a group of values for the config entry."""
    return {
        "observed": observed.isoformat(),
        "updated": updated.isoformat(),
        "values": values,
    }
