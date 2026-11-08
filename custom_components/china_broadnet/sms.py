"""Match complete, explicitly configured China Broadnet SMS formats."""

import hashlib
import re
import unicodedata
from collections.abc import Mapping
from datetime import UTC, datetime

from .const import SMS_WINDOW, TRAFFIC_KEYS
from .usage import NUMBER, amount, make_report, traffic_gb

MAX_CONTENT = 2048
UNITS = r"B|[KMG]I?B"
OTP = re.compile(r"验证码|校验码|动态密码|一次性密码|verification code|\bOTP\b", re.I)


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).replace("\r\n", "\n").strip()


def compile_template(template: str, group: str) -> re.Pattern:
    """Compile fixed text with bounded amount placeholders."""
    template = _normalize(template)
    if len(template) > MAX_CONTENT or OTP.search(template):
        raise ValueError("Invalid template")

    allowed = ("balance",) if group == "balance" else TRAFFIC_KEYS
    parts = []
    seen = set()
    for index, part in enumerate(re.split(r"\{([^{}]*)\}", template)):
        if index % 2 == 0:
            if "{" in part or "}" in part:
                raise ValueError("Invalid literal")
            parts.append(re.escape(part))
            continue
        if part not in allowed or part in seen or not parts[-1]:
            raise ValueError("Invalid placeholder")
        seen.add(part)
        if part == "balance":
            parts.append(rf"(?P<balance>-?{NUMBER})[ \t]*元")
        else:
            parts.append(rf"(?P<{part}>{NUMBER})[ \t]*(?P<{part}_unit>{UNITS})")
    if not seen or len(template) <= sum(len(key) + 2 for key in seen):
        raise ValueError("A template needs fixed text and at least one measurement")
    return re.compile("".join(parts), re.IGNORECASE)


def parse_sms(
    form: Mapping,
    sender: str,
    templates: dict[str, re.Pattern],
    base: int,
    now: datetime,
    reports: dict,
) -> tuple[str, dict] | None:
    """Validate a forwarded message and return a new report, if any."""
    content = form.get("content")
    stamp = form.get("timestamp")
    if (
        form.get("from") != sender
        or not isinstance(content, str)
        or not 0 < len(content) <= MAX_CONTENT
        or not isinstance(stamp, str)
        or not re.fullmatch(r"[0-9]{13}", stamp)
    ):
        return None

    observed = datetime.fromtimestamp(int(stamp) / 1000, UTC)
    content = _normalize(content)
    if abs(now - observed) > SMS_WINDOW or OTP.search(content):
        return None

    matches = [
        (group, match)
        for group, pattern in templates.items()
        if (match := pattern.fullmatch(content))
    ]
    if len(matches) != 1:
        return None

    group, match = matches[0]
    digest = hashlib.sha256(content.encode()).hexdigest()
    if previous := reports.get(group):
        prior = datetime.fromisoformat(previous["observed"])
        if observed <= prior or (
            digest == previous.get("digest") and observed - prior <= SMS_WINDOW
        ):
            return None

    if group == "balance":
        values = {"balance": float(amount(match["balance"], signed=True))}
    else:
        values = {
            key: traffic_gb(match[key], match[f"{key}_unit"], base)
            for key in TRAFFIC_KEYS
            if key in match.groupdict()
        }
    return group, {**make_report(values, observed, now), "digest": digest}
