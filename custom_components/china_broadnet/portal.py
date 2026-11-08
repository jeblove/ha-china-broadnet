"""Read account values with the public website request format."""

import base64
import hashlib
import json
import re
import shlex
from datetime import UTC, datetime
from urllib.parse import quote

from aiohttp import ClientError, ClientSession, ClientTimeout
from cryptography.hazmat.primitives import padding as symmetric_padding
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.serialization import load_der_public_key

from .usage import SHANGHAI, traffic_gb

ORIGIN = "https://www.10099.com.cn"
ACCOUNT_PAGE = f"{ORIGIN}/personal-center-number-order.html"
TIMEOUT = ClientTimeout(total=12)
MAX_RESPONSE = 256 * 1024
CHANNEL = "cd_20220516_093342"
CLIENT_VERSION = "1.1.7.0915_release"

# Public interoperability constants delivered to every website visitor.
_SESSION_KEY = b"N2841A3412APCD6F"
_SESSION_IV = b"AUCDTF12H41P34Y2"
_REQUEST_PUBLIC_KEY = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA4QJmWsYFtBbd/yoySbBAy7pBbfrg"
    "Hr2SGSOGoBukd65IUqQ9uWhvzCN9r0nWESIXWW6FR2A5adHUFFeETucXGjU61BZxbLLf0ARL"
    "4F1NkkFMUF/D3GMY401+6TK8jVMxy4vLb6EKzlyZaI8jKBJ9jh0HMli6U7JHydsXstGRUnyx"
    "8Mu11bJ3+ZMUcKe51Zi+85Ez756EdZhGTXSY7pUAvh8/0Fea6mtsOs9OLMHbGMKAOE0alN1Q"
    "dfqE3QMKgB58MVwlPY7uljelSocjNxCIS58CLvu1iWFrLvsCp8t3DavyA1OD/PPcXRrNLYZg"
    "zG5304/LAqfurOpU35AaB5tOnwIDAQAB"
)
_PUBLIC_KEY = load_der_public_key(base64.b64decode(_REQUEST_PUBLIC_KEY))
assert isinstance(_PUBLIC_KEY, rsa.RSAPublicKey)
_REQUEST_BLOCK_SIZE = _PUBLIC_KEY.key_size // 8 - 12


class PortalError(Exception):
    """The official website query failed."""


class PortalAuthError(PortalError):
    """The website session or its cookies need to be refreshed."""


class PortalChallengeError(PortalError):
    """The request reached the website's browser verification page."""


class PortalCurlError(ValueError):
    """The browser's copied request cannot supply the required headers."""


def parse_portal_curl(command: str) -> dict[str, str]:
    """Extract headers from browser-exported cURL without running it."""
    error = "Expected a copied balance request with Cookie and User-Agent"
    if len(command) > 32_768:
        raise PortalCurlError(error)
    try:
        parts = shlex.split(re.sub(r"\\\r?\n", "", command))
    except ValueError:
        raise PortalCurlError(error) from None
    if not parts or parts.pop(0) != "curl":
        raise PortalCurlError(error)

    url = f"{ORIGIN}/contact-web/api/busi/qryBalanceFee"
    found_url = False
    headers = {}
    arguments = iter(parts)
    for part in arguments:
        if part == "--url" or part.startswith("--url="):
            part = next(arguments, "") if part == "--url" else part[6:]
            if part != url or found_url:
                raise PortalCurlError(error)
        if part == url and not found_url:
            found_url = True
            continue
        if part in ("--compressed", "--globoff", "--path-as-is"):
            continue
        value = next(arguments, None)
        if value is None:
            raise PortalCurlError(error)
        if part in ("-H", "--header"):
            name, _, value = value.partition(":")
            name = name.strip().lower()
            if name not in ("cookie", "user-agent"):
                continue
        elif part in ("-b", "--cookie"):
            name = "cookie"
        elif part in ("-A", "--user-agent"):
            name = "user-agent"
        elif part in ("-d", "--data", "--data-raw", "--data-binary"):
            continue
        elif part in ("-X", "--request") and value == "POST":
            continue
        else:
            raise PortalCurlError(error)
        if name in headers:
            raise PortalCurlError(error)
        headers[name] = value.strip()
    if (
        not found_url
        or not re.fullmatch(r"[ -~]{1,8192}", headers.get("cookie", ""))
        or "=" not in headers["cookie"]
        or not re.fullmatch(r"[ -~]{1,1024}", headers.get("user-agent", ""))
    ):
        raise PortalCurlError(error)
    return {"cookie": headers["cookie"], "user_agent": headers["user-agent"]}


def _storage_value(value: str, field: str) -> str:
    """Read one value from the website's exported tab storage."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Fa-f0-9]{32,4096}", value):
        raise ValueError("Invalid website session")
    decryptor = Cipher(algorithms.AES(_SESSION_KEY), modes.CBC(_SESSION_IV)).decryptor()
    unpad = symmetric_padding.PKCS7(128).unpadder()
    clear = decryptor.update(bytes.fromhex(value)) + decryptor.finalize()
    decoded = json.loads(unpad.update(clear) + unpad.finalize())
    if not isinstance(decoded, dict) or not isinstance(
        result := decoded.get(field), str
    ):
        raise ValueError("Invalid website session")
    return result


def _request(
    account: str, session_id: str, now: datetime
) -> tuple[str, dict[str, str]]:
    """Produce the public website checksum and encrypted request envelope."""
    data = {
        "channelId": CHANNEL,
        "accessNum": account,
        "sessionId": session_id,
        "v": CLIENT_VERSION,
        "timestamp": int(now.timestamp() * 1000),
    }
    checksum = "&".join(f"{key}={data[key]}" for key in sorted(data))
    access = hashlib.md5(checksum.encode(), usedforsecurity=False).hexdigest()
    encoded = quote(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")), safe="~!*'()"
    ).encode()
    encrypted = b"".join(
        _PUBLIC_KEY.encrypt(
            encoded[offset : offset + _REQUEST_BLOCK_SIZE], padding.PKCS1v15()
        )
        for offset in range(0, len(encoded), _REQUEST_BLOCK_SIZE)
    )
    return access, {"data": base64.b64encode(encrypted).decode()}


def _integer(value: object, *, signed: bool = False) -> int:
    if not isinstance(value, str) or not re.fullmatch(
        r"-?[0-9]{1,12}" if signed else r"[0-9]{1,12}", value
    ):
        raise ValueError("Invalid website measurement")
    return int(value)


def _traffic(rows: list, package: str, now: datetime) -> dict[str, float]:
    if not package or "通用" not in package or re.search(r"定向|结转|赠送", package):
        return {}
    matches = [
        row
        for row in rows
        if isinstance(row, dict)
        and row.get("busiType") == "5"
        and row.get("discntName") == package
    ]
    if len(matches) != 1:
        return {}
    row = matches[0]
    local = now.astimezone(SHANGHAI)
    month = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month = month.replace(
        year=month.year + (month.month == 12), month=month.month % 12 + 1
    )
    try:
        start = datetime.strptime(row["startTime"], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=SHANGHAI
        )
        end = datetime.strptime(row["endTime"], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=SHANGHAI
        )
        total = _integer(row.get("highFee"))
        remaining = _integer(row.get("balance"))
    except KeyError, TypeError, ValueError:
        return {}
    if not month <= start <= local < end <= next_month or remaining > total:
        return {}
    return {
        "data_total": traffic_gb(str(total), "KiB", 1024),
        "data_used": traffic_gb(str(total - remaining), "KiB", 1024),
        "data_remaining": traffic_gb(str(remaining), "KiB", 1024),
    }


class PortalClient:
    """Query the website read-only routes with an imported account session."""

    def __init__(self, http: ClientSession, settings: dict) -> None:
        self._http = http
        raw = settings["session"]
        if not isinstance(raw, str) or len(raw) > 10_000:
            raise ValueError("Invalid website session")
        storage = json.loads(raw)
        if not isinstance(storage, dict) or set(storage) != {
            "broadnetUserPhoneInfo",
            "broadnetUserSessionId",
        }:
            raise ValueError("Invalid website session")
        self.account = _storage_value(storage["broadnetUserPhoneInfo"], "accessNumber")
        self._session_id = _storage_value(storage["broadnetUserSessionId"], "id")
        if not re.fullmatch(r"1[0-9]{10}", self.account) or not re.fullmatch(
            r"[A-Za-z0-9_.-]{1,256}", self._session_id
        ):
            raise ValueError("Invalid website session")
        cookie = settings["cookie"].strip()
        agent = settings["user_agent"].strip()
        if not re.fullmatch(r"[ -~]{1,8192}", cookie) or not re.fullmatch(
            r"[ -~]{1,1024}", agent
        ):
            raise ValueError("Invalid website headers")
        self._headers = {
            "Cookie": cookie,
            "User-Agent": agent,
            "Origin": ORIGIN,
            "Referer": ACCOUNT_PAGE,
            "X-Requested-With": "XMLHttpRequest",
        }
        self._package = settings.get("package", "").strip()
        if len(self._package) > 128:
            raise ValueError("Invalid package name")

    async def _query(self, route: str) -> dict:
        access, payload = _request(self.account, self._session_id, datetime.now(UTC))
        try:
            async with self._http.post(
                f"{ORIGIN}/contact-web/api/busi/{route}",
                headers={**self._headers, "Access": access},
                json=payload,
                timeout=TIMEOUT,
                allow_redirects=False,
            ) as response:
                if response.content_type == "text/html":
                    raise PortalChallengeError("Website returned a verification page")
                if response.status in (401, 403):
                    raise PortalAuthError("Website session rejected")
                if (
                    response.status != 200
                    or response.content_type != "application/json"
                ):
                    raise PortalError("Unexpected website response")
                body = bytearray()
                async for chunk in response.content.iter_chunked(8192):
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE:
                        raise PortalError("Website response exceeds limit")
            envelope = json.loads(body)
        except ClientError, TimeoutError, ValueError, RecursionError:
            raise PortalError("Website request failed") from None

        if not isinstance(envelope, dict):
            raise PortalError("Invalid website response")
        if envelope.get("status") in ("701", "OBC967"):
            raise PortalAuthError("Website session rejected")
        data = envelope.get("data")
        if (
            envelope.get("status") != "000000"
            or envelope.get("ok") is not True
            or not isinstance(data, dict)
            or not isinstance(bean := data.get("intfResultBean"), dict)
        ):
            raise PortalError("Invalid website response")
        return bean

    async def async_balance(self) -> float:
        """Validate the session and read the website's displayed account balance."""
        balance = await self._query("qryBalanceFee")
        try:
            overdue = balance.get("spayFee")
            if overdue and (arrears := _integer(overdue)):
                return -arrears / 100
            return _integer(balance.get("balance"), signed=True) / 100
        except ValueError:
            raise PortalError("Invalid balance response") from None

    async def async_read(self) -> dict:
        """Return balance and one explicitly confirmed monthly general package."""
        balance = await self.async_balance()
        traffic = {}
        if self._package:
            resources = await self._query("qryUserRes")
            if not isinstance(rows := resources.get("userResList"), list):
                raise PortalError("Invalid traffic response")
            traffic = _traffic(rows, self._package, datetime.now(UTC))
        return {"balance": balance, **traffic, "last_updated": datetime.now(UTC)}
