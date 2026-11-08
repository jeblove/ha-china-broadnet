"""Fetch and display a short-lived official login CAPTCHA."""

from collections.abc import Callable
from datetime import UTC, datetime

from aiohttp import ClientError, ClientSession
from aiohttp.web import Response
from yarl import URL

from homeassistant.components import webhook
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN
from .portal import (
    CHANNEL,
    CLIENT_VERSION,
    ORIGIN,
    TIMEOUT,
    PortalChallengeError,
    PortalCodeError,
    PortalError,
    _encode_request,
    _post_envelope,
    _valid_session_id,
)

LOGIN_PAGE = f"{ORIGIN}/login.html"
CAPTCHA_URL = f"{ORIGIN}/contact-web/api/login/getImageCheckCode"
CAPTCHA_LIFETIME = 120
MAX_IMAGE_SIZE = 64 * 1024
IMAGE_SIGNATURES = {"image/jpeg": b"\xff\xd8\xff", "image/png": b"\x89PNG\r\n\x1a\n"}
DESKTOP_AGENT = "Home Assistant China Broadnet"


async def async_fetch_captcha(http: ClientSession) -> tuple[str, bytes]:
    """Read only a small JPEG or PNG challenge from the carrier login origin."""
    try:
        async with http.get(
            CAPTCHA_URL,
            headers={"Referer": LOGIN_PAGE, "User-Agent": DESKTOP_AGENT},
            allow_redirects=False,
            timeout=TIMEOUT,
        ) as response:
            mime = response.content_type
            if mime == "text/html":
                raise PortalChallengeError("Website returned a verification page")
            signature = IMAGE_SIGNATURES.get(mime)
            if response.status != 200 or signature is None:
                raise PortalError("Unexpected CAPTCHA response")
            body = bytearray()
            async for chunk in response.content.iter_chunked(8192):
                body.extend(chunk)
                if len(body) > MAX_IMAGE_SIZE:
                    raise PortalError("CAPTCHA image exceeds limit")
            if not body.startswith(signature):
                raise PortalError("Invalid CAPTCHA image")
            return mime, bytes(body)
    except ClientError, TimeoutError:
        raise PortalError("CAPTCHA request failed") from None


async def _async_desktop_request(http: ClientSession, route: str, params: dict) -> dict:
    access, payload = _encode_request(
        {
            **params,
            "channelId": CHANNEL,
            "v": CLIENT_VERSION,
            "timestamp": int(datetime.now(UTC).timestamp() * 1000),
        }
    )
    envelope, _ = await _post_envelope(
        http,
        f"{ORIGIN}/contact-web/api/login/{route}",
        {
            "Access": access,
            "Origin": ORIGIN,
            "Referer": LOGIN_PAGE,
            "User-Agent": DESKTOP_AGENT,
            "X-Requested-With": "XMLHttpRequest",
        },
        payload,
    )
    if envelope.get("status") != "000000":
        raise PortalCodeError("Website rejected the login request")
    data = envelope.get("data")
    if not isinstance(data, dict):
        raise PortalError("Invalid website login response")
    return data


async def async_desktop_send_sms(http: ClientSession, phone: str) -> None:
    """Send a login SMS from the same browserless CAPTCHA session."""
    await _async_desktop_request(
        http, "getVerifyCode", {"recieveNum": phone, "type": 5}
    )


async def async_desktop_login(
    http: ClientSession, phone: str, code: str, image_code: str
) -> dict[str, str]:
    """Exchange user-entered verification codes for desktop session settings."""
    data = await _async_desktop_request(
        http,
        "gwLogin",
        {
            "loginPhone": phone,
            "loginPassWord": code,
            "loginType": "5",
            "channelType": "1",
            "imageCheckCode": image_code,
        },
    )
    if not _valid_session_id(session := data.get("sessionId")):
        raise PortalError("Invalid website session response")
    cookie = (
        http.cookie_jar.filter_cookies(
            URL(f"{ORIGIN}/contact-web/api/busi/qryBalanceFee")
        )
        .output(header="", sep=";")
        .strip()
    )
    if not cookie:
        raise PortalError("Website did not issue session cookies")
    return {"session": session, "cookie": cookie, "user_agent": DESKTOP_AGENT}


@callback
def async_publish_captcha(
    hass: HomeAssistant, mime: str, image: bytes
) -> tuple[str, Callable[[], None]]:
    """Serve a CAPTCHA only through a temporary unguessable local path."""
    webhook_id = webhook.async_generate_id()

    async def handle(_hass, _webhook_id, _request):
        return Response(
            body=image,
            content_type=mime,
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    webhook.async_register(
        hass,
        DOMAIN,
        "China Broadnet CAPTCHA",
        webhook_id,
        handle,
        local_only=True,
        allowed_methods=("GET",),
    )

    @callback
    def expire(_now):
        webhook.async_unregister(hass, webhook_id)

    cancel_expiration = async_call_later(hass, CAPTCHA_LIFETIME, expire)

    @callback
    def remove():
        cancel_expiration()
        webhook.async_unregister(hass, webhook_id)

    return webhook.async_generate_path(webhook_id), remove
