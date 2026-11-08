"""Verify scope resets, change events and persisted monthly snapshots."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from custom_components.china_broadnet.insights import UsageInsights, daily_rate

NOW = datetime(2026, 11, 29, tzinfo=UTC)


def test_estimate_requires_comparable_recorded_history():
    def sample(days, state, scope="one"):
        return SimpleNamespace(
            state=state,
            last_updated=NOW - timedelta(days=days),
            attributes={"usage_scope": scope, "unit_of_measurement": "GB"},
        )

    history = [sample(6, "2"), sample(4, "6"), sample(1, "12")]
    assert daily_rate(history, NOW, 14, "one") == 2
    assert daily_rate([sample(0.5, "12")], NOW, 14, "one") is None
    assert daily_rate([*history, sample(0.5, "12", "other")], NOW, 14, "one") is None
    assert daily_rate([*history, sample(0.5, "16")], NOW, 14, "one") is None


async def test_live_changes_and_archive_survive_reload(hass):
    insights = UsageInsights(hass, "synthetic")
    events = []
    hass.bus.async_listen(
        "china_broadnet_usage_spike", lambda event: events.append(event)
    )
    hass.bus.async_listen(
        "china_broadnet_packages_changed", lambda event: events.append(event)
    )
    pack = {
        "category": "bonus",
        "name": "example",
        "start": NOW,
        "end": NOW + timedelta(days=1),
    }

    async def apply(now, used, packages, scope="one", resources_ok=True):
        data = {
            "packages": packages,
            "resources_ok": resources_ok,
            "usage_scope": scope,
            "data_used": used,
            "monthly_spend": 0.0,
            "voice_remaining": 30,
        }
        await insights.async_apply(data, now)
        await hass.async_block_till_done()
        return data

    await apply(NOW, 1, {})
    await apply(NOW + timedelta(minutes=20), 3, {"first": pack})
    await apply(NOW + timedelta(minutes=40), 7, {"first": pack})
    await apply(NOW + timedelta(minutes=50), 8, {}, resources_ok=False)
    await apply(NOW + timedelta(minutes=51), 20, {"first": pack}, scope="two")
    assert [event.event_type for event in events] == [
        "china_broadnet_packages_changed",
        "china_broadnet_usage_spike",
    ]
    assert events[1].data["used_gb"] == 6

    renewed = {**pack, "end": NOW + timedelta(days=2)}
    await apply(NOW + timedelta(minutes=52), 20, {"second": renewed}, scope="two")
    assert events[-1].data["changes"][0]["type"] == "renewed"
    insights = UsageInsights(hass, "synthetic")
    (november,) = await insights.async_archive(NOW + timedelta(days=4))
    assert november["month"] == "2026-11"
    assert november["readings"]["data_used"]["value"] == 20
    assert november["readings"]["monthly_spend"]["value"] == 0
    assert (
        november["readings"]["data_used"]["observed_at"]
        == (NOW + timedelta(minutes=52)).isoformat()
    )
    await insights.store.async_remove()
