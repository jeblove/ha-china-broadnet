"""Accept bounded submissions through a local-only webhook."""

import asyncio
import json
import re
from datetime import UTC, datetime
from urllib.parse import parse_qsl

from aiohttp.web import Request, Response

from homeassistant.components import webhook
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, SMS_WINDOW, TRAFFIC_KEYS
from .coordinator import BroadnetEntry
from .sms import compile_template, parse_sms
from .usage import amount, make_report

MAX_BODY = 24_576
READ_TIMEOUT = 5
RECEIVER_SETTINGS = (
    "webhook_id",
    "sender",
    "unit_base",
    "balance_template",
    "traffic_template",
)


@callback
def async_register_receiver(hass: HomeAssistant, entry: BroadnetEntry) -> None:
    """Register a POST-only endpoint with a random per-entry secret."""
    browser = entry.data["source"] == "browser"
    keys = ("webhook_id",) if browser else RECEIVER_SETTINGS
    settings = {key: entry.data[key] for key in keys}
    webhook_id = settings["webhook_id"]
    templates = {
        group: compile_template(template, group)
        for group in ("balance", "traffic")
        if (template := settings.get(f"{group}_template"))
    }
    sender = settings.get("sender", "")
    base = int(settings.get("unit_base", "1000"))
    content_type = (
        "application/json" if browser else "application/x-www-form-urlencoded"
    )
    active = True

    async def handle(_hass: HomeAssistant, _webhook_id: str, request: Request):
        if request.content_type != content_type:
            return Response(status=415)
        if request.content_length is not None and request.content_length > MAX_BODY:
            return Response(status=413)

        body = bytearray()
        try:
            async with asyncio.timeout(READ_TIMEOUT):
                async for chunk in request.content.iter_chunked(4096):
                    body.extend(chunk)
                    if len(body) > MAX_BODY:
                        return Response(status=413)
            if not active or any(
                entry.data[key] != value for key, value in settings.items()
            ):
                return Response(status=410)
            now = datetime.now(UTC)
            reports = entry.data["reports"]
            if browser:
                updates = browser_reports(json.loads(body), now, reports)
                if updates:
                    entry.runtime_data.async_accept_reports(updates, now)
                return Response(status=204)
            pairs = parse_qsl(
                body.decode(),
                keep_blank_values=True,
                max_num_fields=8,
                errors="strict",
                strict_parsing=True,
            )
        except TimeoutError:
            return Response(status=408)
        except ValueError, OSError:
            return Response(status=400)

        form = dict(pairs)
        if len(form) != len(pairs):
            return Response(status=400)
        if (
            result := parse_sms(form, sender, templates, base, now, reports)
        ) is not None:
            group, report = result
            entry.runtime_data.async_accept_reports({group: report}, now)

        # The same response for accepted and ignored messages avoids exposing state.
        return Response(status=200)

    def unregister() -> None:
        nonlocal active
        active = False
        webhook.async_unregister(hass, webhook_id)

    webhook.async_register(
        hass,
        DOMAIN,
        "China Broadnet",
        webhook_id,
        handle,
        local_only=True,
        allowed_methods=["POST"],
    )
    entry.async_on_unload(unregister)


def browser_reports(payload: dict, now: datetime, reports: dict) -> dict:
    """Validate the complete browser snapshot and reject stale deliveries."""
    if not isinstance(payload, dict) or set(payload) != {
        "observed",
        "balance",
        "traffic",
    }:
        raise ValueError("Invalid snapshot")
    stamp = payload["observed"]
    if not isinstance(stamp, str) or not re.fullmatch(r"[0-9]{13}", stamp):
        raise ValueError("Invalid timestamp")
    observed = datetime.fromtimestamp(int(stamp) / 1000, UTC)
    if abs(now - observed) > SMS_WINDOW or any(
        observed <= datetime.fromisoformat(report["observed"])
        for report in reports.values()
    ):
        return {}
    balance = payload["balance"]
    traffic = payload["traffic"]
    if (
        not isinstance(balance, str)
        or not isinstance(traffic, dict)
        or (traffic and set(traffic) != set(TRAFFIC_KEYS))
    ):
        raise ValueError("Invalid measurements")
    if any(not isinstance(value, str) for value in traffic.values()):
        raise ValueError("Invalid traffic measurement")
    values = {key: float(amount(value)) for key, value in traffic.items()}
    if (
        values
        and abs(values["data_total"] - values["data_used"] - values["data_remaining"])
        > 1e-7
    ):
        raise ValueError("Inconsistent traffic measurements")
    return {
        "balance": make_report(
            {"balance": float(amount(balance, signed=True))}, observed, now
        ),
        "traffic": make_report(values, observed, now),
    }
