"""Exercise the published reminder blueprint in Home Assistant."""

from pathlib import Path

import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.components.blueprint.models import Blueprint, BlueprintInputs
from homeassistant.components.blueprint.schemas import BLUEPRINT_SCHEMA
from homeassistant.setup import async_setup_component
from homeassistant.util.yaml import load_yaml_dict

BLUEPRINT = Path("blueprints/automation/china_broadnet/usage_alert.yaml")


@pytest.mark.parametrize(
    ("kind", "alert", "clear"),
    [
        ("low", "10", "30"),
        ("ahead", "30", "10"),
        ("expiry", "2026-11-10T08:00:00+08:00", "2026-11-15T08:00:00+08:00"),
    ],
)
async def test_blueprint_defers_and_deduplicates(hass, freezer, kind, alert, clear):
    await hass.config.async_set_time_zone("Asia/Shanghai")
    freezer.move_to("2026-11-09 15:01:00")
    assert await async_setup_component(
        hass,
        "input_boolean",
        {"input_boolean": {"broadnet_notified": {"initial": False}}},
    )
    inputs = BlueprintInputs(
        Blueprint(load_yaml_dict(BLUEPRINT), schema=BLUEPRINT_SCHEMA),
        {
            "alias": "Broadnet reminder",
            "use_blueprint": {
                "path": str(BLUEPRINT),
                "input": {
                    "watched_entity": "sensor.broadnet_test",
                    "alert_kind": kind,
                    "notified": "input_boolean.broadnet_notified",
                    "quiet_start": "22:00:00",
                    "quiet_end": "07:00:00",
                    "notify_action": [{"event": "broadnet_test_alert"}],
                },
            },
        },
    )
    inputs.validate()
    events = []
    hass.bus.async_listen("broadnet_test_alert", events.append)
    assert await async_setup_component(
        hass, "automation", {"automation": [inputs.async_substitute()]}
    )
    await hass.async_block_till_done()

    async def set_state(value):
        hass.states.async_set("sensor.broadnet_test", value)
        await hass.async_block_till_done()

    await set_state(alert)
    assert events == []
    freezer.move_to("2026-11-09 23:01:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(events) == 1
    await set_state("unavailable")
    await set_state(alert)
    assert len(events) == 1
    await set_state(clear)
    await set_state(alert)
    assert len(events) == 2
    await hass.services.async_call(
        "automation",
        "turn_off",
        {"entity_id": "automation.broadnet_reminder"},
        blocking=True,
    )
