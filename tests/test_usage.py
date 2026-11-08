"""Exercise field semantics and conservative SMS matching with synthetic input."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.china_broadnet.sms import compile_template, parse_sms
from custom_components.china_broadnet.usage import (
    current_values,
    make_report,
    traffic_gb,
)

NOW = datetime(2026, 10, 8, tzinfo=UTC)


@pytest.mark.parametrize(
    ("amount", "unit", "base", "expected"),
    [
        ("1", "B", 1000, 1e-9),
        ("1", "KB", 1000, 1e-6),
        ("1024", "KB", 1024, 0.001048576),
        ("500", "MB", 1000, 0.5),
        ("1024", "MB", 1024, 1.073741824),
        ("1", "GB", 1000, 1),
        ("1", "GB", 1024, 1.073741824),
        ("1", "GiB", 1000, 1.073741824),
        ("0", "MiB", 1000, 0),
    ],
)
def test_units(amount, unit, base, expected):
    assert traffic_gb(amount, unit, base) == pytest.approx(expected)


@pytest.mark.parametrize(
    "template",
    [
        "{balance}",
        "余额：{Balance}",
        "余额{balance}{balance}",
        "验证码{balance}",
        "{",
    ],
)
def test_invalid_templates(template):
    with pytest.raises(ValueError, match="."):
        compile_template(template, "balance")


def test_sms_uses_only_explicitly_mapped_package():
    pattern = compile_template(
        "示例月包通用剩余：{data_remaining}；"
        "定向剩余：2GB；赠送包：3GB（7天有效）",
        "traffic",
    )
    form = {
        "from": "10099",
        "content": (
            "示例月包通用剩余：５００MB；"
            "定向剩余：2GB；赠送包：3GB（7天有效）"
        ),
        "timestamp": str(int(NOW.timestamp() * 1000)),
    }
    args = ("10099", {"traffic": pattern}, 1000)
    group, report = parse_sms(form, *args, NOW, {})
    assert group == "traffic"
    assert report["values"] == {"data_remaining": 0.5}
    assert parse_sms(form, *args, NOW, {group: report}) is None
    for key, value in (
        ("from", "99999"),
        ("timestamp", "1"),
        ("content", form["content"].replace("定向剩余", "结转剩余")),
        ("content", form["content"].replace("7天", "8天")),
        ("content", form["content"] + "；验证码 123456"),
    ):
        assert parse_sms({**form, key: value}, *args, NOW, {}) is None

    retry = NOW + timedelta(minutes=1)
    form["timestamp"] = str(int(retry.timestamp() * 1000))
    assert parse_sms(form, *args, retry, {group: report}) is None


def test_out_of_order_and_old_messages():
    templates = {"balance": compile_template("示例余额：{balance}", "balance")}
    form = {"from": "10099", "content": "示例余额：0元"}

    def parse_at(when, reports):
        stamped = {**form, "timestamp": str(int(when.timestamp() * 1000))}
        return parse_sms(stamped, "10099", templates, 1000, NOW, reports)

    _, report = parse_at(NOW, {})
    assert report["values"] == {"balance": 0}
    assert parse_at(NOW - timedelta(minutes=1), {"balance": report}) is None
    assert parse_at(NOW - timedelta(minutes=11), {}) is None
    assert parse_at(NOW + timedelta(minutes=11), {}) is None


def test_groups_expire_independently_and_traffic_resets_at_china_month_boundary():
    boundary = datetime(2026, 10, 31, 16, tzinfo=UTC)
    before = boundary - timedelta(minutes=1)
    balance = make_report({"balance": 0}, before, before)
    traffic = make_report({"data_used": 1.2}, before, before)
    reports = {"balance": balance, "traffic": traffic}
    assert current_values(reports, before) == {
        "balance": 0, "data_used": 1.2, "last_updated": before
    }
    assert current_values(reports, boundary) == {"balance": 0, "last_updated": before}
    assert current_values(reports, before + timedelta(hours=48)) == {
        "last_updated": before
    }
