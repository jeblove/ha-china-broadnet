"""Accept bounded SmsForwarder form submissions through a local-only webhook."""

import asyncio
from datetime import UTC, datetime
from urllib.parse import parse_qsl

from aiohttp.web import Request, Response

from homeassistant.components import webhook
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .coordinator import BroadnetEntry
from .sms import compile_template, parse_sms

MAX_BODY = 24_576
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
    settings = {key: entry.data[key] for key in RECEIVER_SETTINGS}
    webhook_id = settings["webhook_id"]
    templates = {
        group: compile_template(template, group)
        for group in ("balance", "traffic")
        if (template := settings[f"{group}_template"])
    }
    sender = settings["sender"]
    base = int(settings["unit_base"])
    active = True

    async def handle(_hass: HomeAssistant, _webhook_id: str, request: Request):
        if request.content_type != "application/x-www-form-urlencoded":
            return Response(status=415)
        if request.content_length is not None and request.content_length > MAX_BODY:
            return Response(status=413)

        body = bytearray()
        try:
            async with asyncio.timeout(5):
                async for chunk in request.content.iter_chunked(4096):
                    body.extend(chunk)
                    if len(body) > MAX_BODY:
                        return Response(status=413)
            pairs = parse_qsl(
                body.decode(), max_num_fields=8, errors="strict", strict_parsing=True
            )
        except TimeoutError:
            return Response(status=408)
        except (ValueError, OSError):
            return Response(status=400)

        form = dict(pairs)
        if len(form) != len(pairs):
            return Response(status=400)
        if active and all(entry.data[key] == value for key, value in settings.items()):
            now = datetime.now(UTC)
            result = parse_sms(form, sender, templates, base, now, entry.data["reports"])
            if result is not None:
                group, report = result
                entry.runtime_data.async_accept_report(group, report, now)

        # The same response for accepted and ignored messages avoids exposing state.
        return Response(status=200)

    def unregister() -> None:
        nonlocal active
        active = False
        webhook.async_unregister(hass, webhook_id)

    webhook.async_register(
        hass, DOMAIN, "China Broadnet", webhook_id, handle,
        local_only=True, allowed_methods=["POST"],
    )
    entry.async_on_unload(unregister)
