"""Export a bounded, allowlisted description of the latest query."""

from collections import Counter

from homeassistant.loader import async_get_integration

from .const import DEFAULT_REFRESH_MINUTES, DOMAIN, TRAFFIC_KEYS

REJECTION_REASONS = (
    "invalid_row",
    "invalid_label",
    "unclassified",
    "shared_or_unknown_owner",
    "invalid_fields",
    "inactive",
    "outside_cycle_or_invalid_total",
    "ambiguous_identities",
)
CATEGORIES = ("general", "rollover", "directed", "bonus")
SECTIONS = ("resources", "selected_packages", "plan")
READINGS = (
    "balance",
    "monthly_spend",
    "voice_remaining",
    "previous_bill",
    "outside_total_fee",
    "plan_name",
    *TRAFFIC_KEYS,
)
SOURCES = ("portal", "manual", "sms")
PORTAL_MODES = ("h5", "desktop")


async def async_get_config_entry_diagnostics(hass, entry):
    """Report availability and counts without exporting names or account data."""
    coordinator = getattr(entry, "runtime_data", None)
    data = (coordinator.data or {}) if coordinator else {}
    source = entry.data.get("source")
    mode = entry.data.get("portal_mode")
    rejected = data.get("package_rejections", {})
    counts = Counter(item["category"] for item in data.get("packages", {}).values())
    return {
        "integration_version": (await async_get_integration(hass, DOMAIN)).version,
        "refresh_interval_minutes": (
            entry.data.get("refresh_minutes", DEFAULT_REFRESH_MINUTES)
            if source == "portal"
            else None
        ),
        "last_successful_queries": {
            key: timestamp.isoformat() if timestamp is not None else None
            for key, timestamp in {
                "balance": coordinator.balance_updated if coordinator else None,
                "resources": coordinator.resources_updated if coordinator else None,
                "overall": data.get("last_updated"),
            }.items()
        },
        "verification_retry_after_seconds": (
            coordinator.verification_retry_after_seconds if coordinator else 0
        ),
        "source": source if source in SOURCES else "unknown",
        "portal_mode": mode if mode in PORTAL_MODES else None,
        "query_status": coordinator.query_status
        if coordinator and source == "portal"
        else None,
        "last_update_success": coordinator.last_update_success if coordinator else None,
        "missing_sections": [
            section
            for section in SECTIONS
            if section in data.get("missing_sections", ())
        ],
        "reading_present": {key: data.get(key) is not None for key in READINGS},
        "package_counts": {key: counts[key] for key in CATEGORIES if counts[key]},
        "package_rejections": {
            key: rejected[key]
            for key in REJECTION_REASONS
            if type(rejected.get(key)) is int
        },
    }
