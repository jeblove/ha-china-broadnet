"""Exercise setup, reconfiguration, webhook and entity lifecycle in Home Assistant."""

from datetime import UTC, datetime, timedelta


from homeassistant import config_entries
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.china_broadnet.const import DOMAIN


async def start_flow(hass, source):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": source}
    )


async def submit(hass, flow, values):
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], values)
    await hass.async_block_till_done()
    return result


def state(hass, entry, key):
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{key}")
    assert entity_id is not None
    return hass.states.get(entity_id).state


async def test_manual_entry_reconfigure_reload_remove(hass: HomeAssistant):
    form = await start_flow(hass, "manual")
    invalid = await submit(hass, form, {"name": "Test", "balance": "NaN"})
    assert invalid["errors"] == {"balance": "invalid_amount"}
    created = await submit(
        hass, form, {"name": "Test", "balance": "0", "data_total": "10.5"}
    )
    assert created["type"] is FlowResultType.CREATE_ENTRY
    entry = created["result"]
    assert entry.state is config_entries.ConfigEntryState.LOADED
    assert state(hass, entry, "balance") == "0.0"
    assert state(hass, entry, "data_total") == "10.5"
    assert state(hass, entry, "data_used") == STATE_UNAVAILABLE
    assert state(hass, entry, "last_updated") != STATE_UNAVAILABLE

    duplicate = await submit(hass, await start_flow(hass, "manual"), {"name": " test "})
    assert duplicate["reason"] == "already_configured"
    flow = await entry.async_start_reconfigure_flow(hass)
    assert flow["step_id"] == "manual_update"
    assert (await submit(hass, flow, {}))["errors"] == {"base": "missing_values"}
    result = await submit(hass, flow, {"data_remaining": "8.5"})
    assert result["reason"] == "reconfigure_successful"
    assert state(hass, entry, "balance") == "0.0"
    assert state(hass, entry, "data_remaining") == "8.5"
    assert state(hass, entry, "data_total") == STATE_UNAVAILABLE
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert state(hass, entry, "data_remaining") == "8.5"
    await hass.config_entries.async_remove(entry.entry_id)
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_sms_push_rotate_and_expiry(hass: HomeAssistant, hass_client, freezer):
    freezer.move_to(datetime(2026, 10, 8, tzinfo=UTC))
    form = await start_flow(hass, "sms")
    settings = {
        "sender": "10099",
        "unit_base": "1000",
        "balance_template": "示例余额：{balance}",
        "traffic_template": (
            "示例月包：共{data_total}，已用{data_used}，剩余{data_remaining}"
        ),
    }
    assert (await submit(hass, form, {"name": "Test", **settings, "balance_template": "bad"}))[
        "errors"
    ] == {"balance_template": "invalid_template"}
    entry = (await submit(hass, form, {"name": "Test", **settings}))["result"]
    client = await hass_client()

    def path():
        return f"/api/webhook/{entry.data['webhook_id']}"

    async def send(path, message, sender="10099"):
        response = await client.post(
            path,
            data={
                "from": sender,
                "content": message,
                "timestamp": str(int(dt_util.utcnow().timestamp() * 1000)),
            },
        )
        assert response.status == 200

    old_path = path()
    await send(old_path, "示例余额：9元", sender="00000")
    assert state(hass, entry, "balance") == STATE_UNAVAILABLE
    await send(old_path, "示例余额：0元")
    assert state(hass, entry, "balance") == "0.0"
    await send(old_path, "示例月包：共2GB，已用500MB，剩余1500MB")
    assert state(hass, entry, "data_total") == "2.0"
    assert state(hass, entry, "data_used") == "0.5"
    assert state(hass, entry, "data_remaining") == "1.5"

    first = state(hass, entry, "last_updated")
    freezer.tick(timedelta(minutes=1))
    await send(old_path, "示例余额：0元")
    assert state(hass, entry, "last_updated") == first
    assert (await client.post(old_path, json={})).status == 415
    assert (await client.post(old_path, data={"content": "x" * 25_000})).status == 413

    flow = await entry.async_start_reconfigure_flow(hass)
    assert flow["description_placeholders"]["path"] == old_path
    result = await submit(hass, flow, {**settings, "rotate": True})
    assert result["reason"] == "reconfigure_successful"
    assert path() != old_path
    await send(old_path, "示例余额：3元")
    assert state(hass, entry, "balance") == "0.0"
    await send(path(), "示例余额：3元")
    assert state(hass, entry, "balance") == "3.0"

    freezer.tick(timedelta(hours=49))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert state(hass, entry, "balance") == STATE_UNAVAILABLE
    assert state(hass, entry, "data_remaining") == STATE_UNAVAILABLE
    assert state(hass, entry, "last_updated") != STATE_UNAVAILABLE

    saved_path = path()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await send(saved_path, "示例余额：4元")
    assert entry.data["reports"]["balance"]["values"]["balance"] == 3
