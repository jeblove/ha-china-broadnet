"""Verify website package classification with synthetic resources."""

from datetime import UTC, datetime

import pytest

from custom_components.china_broadnet.portal import _traffic

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
    assert _traffic([directed, PLAN], PLAN["discntName"], NOW) == pytest.approx(
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
    assert _traffic(rows, PLAN["discntName"], NOW) == {}
