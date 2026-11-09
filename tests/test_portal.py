"""Verify website package classification with synthetic resources."""

from datetime import UTC, datetime

import pytest

from custom_components.china_broadnet.portal import (
    ORIGIN,
    PortalCurlError,
    _directed_usage,
    _packages,
    _traffic,
    parse_portal_curl,
)

NOW = datetime(2026, 11, 9, tzinfo=UTC)
PLAN = {
    "busiType": "5",
    "discntName": "示例本月套餐通用流量",
    "startTime": "2026-11-01 00:00:00",
    "endTime": "2026-12-01 00:00:00",
    "highFee": "1048576",
    "balance": "524288",
}


def test_select_confirmed_monthly_general_package():
    directed = {**PLAN, "discntName": "示例定向赠送流量", "highFee": "10485760"}
    assert _traffic(_packages([directed, PLAN], NOW), None) == pytest.approx(
        {
            "data_total": 1.073741824,
            "data_used": 0.536870912,
            "data_remaining": 0.536870912,
        }
    )


@pytest.mark.parametrize(
    "rows",
    [
        [PLAN, PLAN],
        [{**PLAN, "startTime": "2026-11-10 00:00:00"}],
        [{**PLAN, "endTime": "2026-11-09 07:59:59"}],
        [{**PLAN, "endTime": "2026-12-01 00:00:01"}],
        [{**PLAN, "balance": "9999999"}],
        [{**PLAN, "highFee": None}],
    ],
)
def test_skip_ambiguous_or_invalid_resource(rows):
    assert _traffic(_packages(rows, NOW), [PLAN["discntName"]]) == {}


BALANCE_URL = f"{ORIGIN}/contact-web/api/busi/qryBalanceFee"


@pytest.mark.parametrize("url_option", ["", "--url ", "--url="])
def test_import_browser_curl(url_option):
    command = " \\\r\n".join(
        (
            f"curl {url_option}'{BALANCE_URL}'",
            "-H 'accept: application/json'",
            "-b 'example=authorized; other=one'",
            "-H 'User-Agent: Example Browser/1.0'",
            '--data-raw \'{"data":"example"}\'',
            "--compressed",
        )
    )
    assert parse_portal_curl(command) == {
        "cookie": "example=authorized; other=one",
        "user_agent": "Example Browser/1.0",
    }


@pytest.mark.parametrize(
    "command",
    [
        "curl 'https://example.test' -b 'a=b' -A 'Agent'",
        "curl --url 'https://example.test' -b 'a=b' -A 'Agent'",
        f"curl --url '{BALANCE_URL}' --url '{BALANCE_URL}' -b 'a=b' -A 'Agent'",
        f"curl '{BALANCE_URL}' -A 'Agent'",
        f"curl '{BALANCE_URL}' -b 'a=b' -b 'a=c' -A 'Agent'",
        f"curl '{BALANCE_URL}' -b 'a=b' -A 'Agent' ; echo unexpected",
    ],
)
def test_reject_invalid_curl(command):
    with pytest.raises(PortalCurlError, match="Expected a copied balance request"):
        parse_portal_curl(command)


def test_multiple_general_and_separate_expirations():
    second = {**PLAN, "discntName": "另一通用流量"}
    bonus = {**PLAN, "discntName": "赠送通用流量"}
    later = {**bonus, "endTime": "2026-12-31 00:00:00"}
    rollover = {**PLAN, "discntName": "上月结转", "startTime": "2026-10-01 00:00:00"}
    packages = _packages([PLAN, second, bonus, later, rollover], NOW)
    assert sorted(pack["category"] for pack in packages.values()) == [
        "bonus",
        "bonus",
        "general",
        "general",
        "rollover",
    ]
    assert _traffic(packages, None)["data_total"] == pytest.approx(2.147483648)
    assert _traffic(packages, [second["discntName"]])["data_total"] == pytest.approx(
        1.073741824
    )
    assert _traffic(packages, []) == {}


def test_directed_ratios_only_aggregate_matching_validity():
    pack = {**PLAN, "discntName": "定向甲", "highFee": "100", "balance": "40"}
    second = {**pack, "discntName": "定向乙", "highFee": "300", "balance": "180"}
    assert _directed_usage(_packages([pack], NOW), NOW) == 60
    assert _directed_usage(_packages([pack, second], NOW), NOW) == 45
    for changed in (
        {**second, "startTime": "2026-11-05 00:00:00"},
        {**second, "highFee": None},
        {**second, "highFee": "0"},
        {**second, "balance": "301"},
        {**second, "ownerType": "unknown"},
    ):
        packages = _packages([pack, changed], NOW)
        assert len(packages) == 2
        assert _directed_usage(packages, NOW) is None
    cross_month = {**second, "endTime": "2027-01-01 00:00:00"}
    assert _directed_usage(_packages([pack, cross_month], NOW), NOW) == 60
