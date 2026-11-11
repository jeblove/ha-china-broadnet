"""Exercise setup, reconfiguration, webhook and entity lifecycle in Home Assistant."""

import asyncio
from base64 import b64encode
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.china_broadnet import receiver
from custom_components.china_broadnet.const import DOMAIN
from homeassistant import config_entries
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util


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


async def reconfigure(hass, entry):
    return await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )


def state(hass, entry, key):
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_{key}"
    )
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
    flow = await reconfigure(hass, entry)
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


async def test_sms_push_rotate_and_expiry(
    hass: HomeAssistant, hass_client, freezer, monkeypatch
):
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
    invalid = await submit(
        hass, form, {"name": "Test", **settings, "balance_template": "bad"}
    )
    assert invalid["errors"] == {"balance_template": "invalid_template"}
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

    async def slow_form():
        yield b"from=10099&"
        await asyncio.Event().wait()

    with monkeypatch.context() as patch:
        patch.setattr(receiver, "READ_TIMEOUT", 0)
        response = await client.post(
            old_path,
            data=slow_form(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert response.status == 408
    assert state(hass, entry, "balance") == "0.0"

    flow = await reconfigure(hass, entry)
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


async def test_native_timer_expires_traffic_at_beijing_midnight(hass, freezer):
    freezer.move_to(datetime(2026, 10, 31, 15, 59, 59, tzinfo=UTC))
    form = await start_flow(hass, "manual")
    entry = (
        await submit(
            hass, form, {"name": "Boundary", "balance": "12", "data_remaining": "1"}
        )
    )["result"]
    assert state(hass, entry, "data_remaining") == "1.0"
    freezer.tick(timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert state(hass, entry, "data_remaining") == STATE_UNAVAILABLE
    assert state(hass, entry, "balance") == "12.0"


def mock_resources(aioclient_mock, balance_url, rows=()):
    aioclient_mock.post(
        balance_url.rsplit("/", 1)[0] + "/qryPhonePackInfo",
        json={"status": "000000", "ok": True, "data": {"packName": "Example plan"}},
        headers={"content-type": "application/json"},
    )
    aioclient_mock.post(
        balance_url.rsplit("/", 1)[0] + "/qryUserRes",
        json={
            "status": "000000",
            "ok": True,
            "data": {
                "intfResultBean": {
                    "userResList": list(rows),
                    "userExtResList": [
                        {"extDiscntFee": "0", "extVoiceFee": "13", "extSmsFee": "25"}
                    ],
                }
            },
        },
        headers={"content-type": "application/json"},
    )


async def test_portal_setup_refresh_and_reauth(hass, aioclient_mock, freezer):
    """Exercise the native HTTP client through the real config entry lifecycle."""
    import json

    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    from custom_components.china_broadnet.portal import (
        _SESSION_IV,
        _SESSION_KEY,
        ORIGIN,
    )

    def protect(value):
        pad = padding.PKCS7(128).padder()
        padded = pad.update(json.dumps(value).encode()) + pad.finalize()
        encrypt = Cipher(
            algorithms.AES(_SESSION_KEY), modes.CBC(_SESSION_IV)
        ).encryptor()
        return (encrypt.update(padded) + encrypt.finalize()).hex()

    def settings(account="19200000000", session_id="example-session"):
        return {
            "session": json.dumps(
                {
                    "broadnetUserPhoneInfo": protect({"accessNumber": account}),
                    "broadnetUserSessionId": protect({"id": session_id}),
                }
            ),
            "curl": (
                f"curl '{url}' -b 'example=authorized' -H 'user-agent: Example browser'"
            ),
        }

    url = f"{ORIGIN}/contact-web/api/busi/qryBalanceFee"
    success = {
        "status": "000000",
        "ok": True,
        "data": {"intfResultBean": {"balance": "1201", "spayFee": "0"}},
    }
    form = await start_flow(hass, "portal")
    data = {"name": "Portal", **settings()}
    invalid = await submit(hass, form, {**data, "curl": "invalid"})
    assert invalid["errors"] == {"curl": "invalid_portal_curl"}
    assert aioclient_mock.call_count == 0
    aioclient_mock.post(
        url, text="<html>Challenge</html>", headers={"content-type": "text/html"}
    )
    blocked = await submit(hass, form, data)
    assert blocked["errors"] == {"base": "portal_challenge"}
    aioclient_mock.clear_requests()
    aioclient_mock.post(url, json=success, headers={"content-type": "application/json"})
    mock_resources(aioclient_mock, url)
    result = await submit(hass, form, data)
    assert result["type"] is FlowResultType.CREATE_ENTRY, result.get("errors")
    entry = result["result"]
    assert state(hass, entry, "balance") == "12.01"

    aioclient_mock.clear_requests()
    response = aioclient_mock.request(
        "POST",
        url,
        json={**success, "data": {"intfResultBean": {"balance": "0"}}},
        headers={"content-type": "application/json"},
    )
    response.cookies.update(SimpleCookie("SERVERID=renewed; Path=/"))
    mock_resources(aioclient_mock, url)
    freezer.tick(timedelta(minutes=8, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert state(hass, entry, "balance") == "0.0"
    assert entry.data["cookie"] == "example=authorized; SERVERID=renewed"
    last_success = state(hass, entry, "last_updated")

    aioclient_mock.clear_requests()
    aioclient_mock.post(
        url, text="<html>Challenge</html>", headers={"content-type": "text/html"}
    )
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert aioclient_mock.mock_calls[-1][3]["Cookie"] == entry.data["cookie"]
    assert state(hass, entry, "balance") == STATE_UNAVAILABLE
    assert state(hass, entry, "last_updated") == last_success
    await entry.runtime_data.async_manual_refresh()
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    freezer.tick(timedelta(minutes=8, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 1

    aioclient_mock.clear_requests()
    aioclient_mock.post(
        url, json={"status": "701"}, headers={"content-type": "application/json"}
    )
    freezer.tick(timedelta(minutes=52))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 1
    (flow,) = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert flow["step_id"] == "reauth_confirm"

    aioclient_mock.clear_requests()
    aioclient_mock.post(url, json=success, headers={"content-type": "application/json"})
    mock_resources(aioclient_mock, url)
    renewed = settings(session_id="new-example-session")
    result = await submit(hass, flow, renewed)
    assert result["reason"] == "reauth_successful"
    assert entry.data["session"] == renewed["session"]
    assert entry.data["cookie"] == "example=authorized"
    assert entry.data["user_agent"] == "Example browser"
    assert "curl" not in entry.data
    assert state(hass, entry, "balance") == "12.01"


async def test_h5_sms_login_and_reauthentication(hass, aioclient_mock, freezer):
    """Only user submission sends an SMS and HA reuses the verified session."""
    from custom_components.china_broadnet.portal import H5_ORIGIN
    from homeassistant.components import webhook
    from homeassistant.util.aiohttp import MockRequest

    freezer.move_to("2026-11-09 00:00:00")
    row = {
        "busiType": "5",
        "highFee": "1048576",
        "balance": "524288",
        "startTime": "2026-11-01 00:00:00",
        "endTime": "2026-12-01 00:00:00",
    }
    rows = [
        {**row, "discntName": name}
        for name in ("示例通用甲", "示例通用乙", "示例定向赠送")
    ]
    rows.append(
        {
            **row,
            "busiType": "1",
            "itemTypeCode": "2",
            "itemName": "语音免费资源",
            "highFee": "200",
            "balance": "0",
        }
    )
    api = f"{H5_ORIGIN}/contact-web/api"
    headers = {"content-type": "application/json"}
    sms_url = f"{api}/login/getVerifyCode"
    login_url = f"{api}/login/gwLogin"
    balance_url = f"{api}/busi/qryBillInfo"
    success = {"status": "000000"}
    data = {"name": "Mobile", "account": "19200000000", "consent": True}

    form = await start_flow(hass, "h5_start")
    invalid = await submit(hass, form, {**data, "account": "192"})
    assert invalid["errors"] == {"account": "invalid_phone"}
    refused = await submit(hass, form, {**data, "consent": False})
    assert refused["errors"] == {"consent": "login_consent"}
    assert aioclient_mock.call_count == 0

    aioclient_mock.post(sms_url, json=success, headers=headers)
    form = await submit(hass, form, data)
    assert form["step_id"] == "h5_code"
    assert aioclient_mock.call_count == 1
    timer = form["description_placeholders"]["timer"].removesuffix(")")
    request = MockRequest(b"", mock_source="test", remote="127.0.0.1")
    countdown = await webhook.async_handle_webhook(
        hass, timer.rsplit("/", 1)[1], request
    )
    assert ">05:00<" in countdown.text
    early = await submit(hass, form, {"resend": True})
    assert early["errors"] == {"base": "sms_cooldown"}
    assert aioclient_mock.call_count == 1
    freezer.tick(timedelta(minutes=5))
    expired = await submit(hass, form, {"code": "123456"})
    assert expired["errors"] == {"code": "sms_expired"}
    assert expired["description_placeholders"]["timer"] == "00:00"
    assert aioclient_mock.call_count == 1
    form = await submit(hass, form, {"resend": True})
    assert form["description_placeholders"]["timer"] != timer + ")"
    assert aioclient_mock.call_count == 2

    aioclient_mock.clear_requests()
    aioclient_mock.post(login_url, json={"status": "DENIED"}, headers=headers)
    invalid = await submit(hass, form, {"code": "123456"})
    assert invalid["errors"] == {"code": "invalid_sms_code"}
    assert aioclient_mock.call_count == 1

    aioclient_mock.clear_requests()
    aioclient_mock.post(
        login_url,
        json={
            **success,
            "data": {"sessionId": "h5-session", "phoneInfo": {"areaCode": "0123"}},
        },
        headers=headers,
    )
    aioclient_mock.post(
        balance_url,
        json={
            **success,
            "data": {
                "balance": "27.42",
                "contentList": [
                    {
                        "title": "当月消费",
                        "list": [{"name": "消费合计", "value": "7.25"}],
                    }
                ],
            },
        },
        headers=headers,
    )
    mock_resources(aioclient_mock, balance_url, rows)
    aioclient_mock.post(
        f"{api}/busi/onlineQueryCustBillMM",
        json={
            "status": "000000",
            "data": {
                "respCode": "000000",
                "intfResultBean": {
                    "custBillList": [{"cycleId": "202610", "feeSum": "2800"}]
                },
            },
        },
        headers=headers,
    )
    result = await submit(hass, form, {"code": "654321"})
    assert result["type"] is FlowResultType.CREATE_ENTRY, result.get("errors")
    entry = result["result"]
    assert entry.data["session"] == "h5-session"
    assert "code" not in entry.data
    assert "consent" not in entry.data
    assert "cookie" not in entry.data
    assert state(hass, entry, "balance") == "27.42"
    assert aioclient_mock.mock_calls[-1][3]["Session"] == "h5-session"

    assert state(hass, entry, "monthly_spend") == "7.25"
    assert state(hass, entry, "outside_data_fee") == "0.0"
    assert state(hass, entry, "outside_voice_fee") == "0.13"
    assert state(hass, entry, "outside_sms_fee") == "0.25"
    assert state(hass, entry, "outside_total_fee") == "0.38"
    assert state(hass, entry, "previous_bill") == "28.0"
    assert state(hass, entry, "query_status") == "success"
    assert float(state(hass, entry, "data_pace")) > 20
    from custom_components.china_broadnet.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    report = await async_get_config_entry_diagnostics(hass, entry)
    assert report["reading_present"]["balance"]
    assert report["package_counts"] == {"general": 2, "directed": 1}
    assert all(
        secret not in repr(report)
        for secret in ("19200000000", "h5-session", "27.42", "示例")
    )

    button = er.async_get(hass).async_get_entity_id(
        "button", DOMAIN, f"{entry.entry_id}_refresh"
    )
    assert button is not None
    calls = aioclient_mock.call_count
    await hass.services.async_call(
        "button", "press", {"entity_id": button}, blocking=True
    )
    assert aioclient_mock.call_count == calls
    freezer.tick(timedelta(seconds=61))
    await hass.services.async_call(
        "button", "press", {"entity_id": button}, blocking=True
    )
    assert aioclient_mock.call_count == calls + 2
    await hass.services.async_call(
        "button", "press", {"entity_id": button}, blocking=True
    )
    assert aioclient_mock.call_count == calls + 2

    resource_timestamp = state(hass, entry, "resources_updated")
    assert state(hass, entry, "voice_remaining") == "0"
    assert state(hass, entry, "plan_name") == "Example plan"
    assert float(state(hass, entry, "data_total")) == 2.147483648
    package_id = next(
        key
        for key, pack in entry.runtime_data.data["packages"].items()
        if pack["category"] == "directed"
    )
    assert float(state(hass, entry, "general_usage_percent")) == 50
    assert float(state(hass, entry, "directed_usage_percent")) == 50
    registry = er.async_get(hass)
    for key in (
        "general_remaining_percent",
        "directed_remaining_percent",
        "estimated_depletion",
        f"package_{package_id}_usage_percent",
    ):
        optional_id = registry.async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_{key}"
        )
        assert (
            registry.async_get(optional_id).disabled_by
            is er.RegistryEntryDisabler.INTEGRATION
        )
        assert hass.states.get(optional_id) is None
    assert float(state(hass, entry, f"package_{package_id}")) == 0.536870912
    assert (
        state(hass, entry, f"package_{package_id}_expiry")
        == "2026-11-30T16:00:00+00:00"
    )
    select = await reconfigure(hass, entry)
    assert select["data_schema"]({})["packages"] == ["示例通用乙", "示例通用甲"]
    assert entry.runtime_data.update_interval == timedelta(minutes=8)
    assert select["data_schema"]({})["refresh_minutes"] == 8
    invalid = await submit(hass, select, {"refresh_minutes": 5.5})
    assert invalid["errors"] == {"refresh_minutes": "invalid_refresh_minutes"}
    selected = await submit(
        hass, invalid, {"packages": ["示例通用甲"], "refresh_minutes": 10}
    )
    assert selected["reason"] == "reconfigure_successful"
    assert entry.data["refresh_minutes"] == 10
    assert entry.runtime_data.update_interval == timedelta(minutes=10)
    assert float(state(hass, entry, "data_total")) == 1.073741824

    duplicate_form = await start_flow(hass, "h5_start")
    duplicate = await submit(hass, duplicate_form, {**data, "name": "Another alias"})
    assert duplicate["reason"] == "already_configured"

    aioclient_mock.clear_requests()
    aioclient_mock.post(
        balance_url, json={**success, "data": {"balance": "30.00"}}, headers=headers
    )
    aioclient_mock.post(f"{api}/busi/qryUserRes", status=503, headers=headers)
    freezer.tick(timedelta(minutes=1))
    await entry.runtime_data.async_refresh()
    assert state(hass, entry, "balance") == "30.0"
    assert state(hass, entry, "query_status") == "partial"
    status_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_query_status"
    )
    assert hass.states.get(status_id).attributes["missing_sections"] == ["resources"]
    assert state(hass, entry, "resources_updated") == resource_timestamp

    aioclient_mock.clear_requests()
    aioclient_mock.post(balance_url, json={"status": "701"}, headers=headers)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    (flow,) = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert flow["step_id"] == "h5_start"
    assert state(hass, entry, "query_status") == "auth_expired"
    assert aioclient_mock.call_count == 1
    freezer.move_to("2026-11-30 16:00:01")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert state(hass, entry, "monthly_snapshots") == "2026-11"
    assert state(hass, entry, "data_total") == STATE_UNAVAILABLE
    assert state(hass, entry, "data_used") == STATE_UNAVAILABLE
    assert state(hass, entry, "query_status") == "auth_expired"
    assert aioclient_mock.call_count == 1

    aioclient_mock.clear_requests()
    aioclient_mock.post(sms_url, json=success, headers=headers)
    form = await submit(hass, flow, {"consent": True})
    assert form["step_id"] == "h5_code"

    aioclient_mock.post(
        login_url,
        json={**success, "data": {"sessionId": "new-h5-session"}},
        headers=headers,
    )
    aioclient_mock.post(
        balance_url, json={**success, "data": {"balance": "0"}}, headers=headers
    )
    mock_resources(aioclient_mock, balance_url)
    result = await submit(hass, form, {"code": "123456"})
    assert result["reason"] == "reauth_successful"
    assert entry.data["session"] == "new-h5-session"
    assert state(hass, entry, "balance") == "0.0"


async def test_desktop_captcha_shows_image_then_requests_sms(
    hass, aioclient_mock, monkeypatch, freezer
):
    """Keep the image local and send no SMS before the user supplies its text."""
    from yarl import URL

    from custom_components.china_broadnet.captcha import CAPTCHA_URL, ORIGIN
    from homeassistant.components import webhook
    from homeassistant.helpers import aiohttp_client
    from homeassistant.util.aiohttp import MockRequest

    create_session = aiohttp_client._async_create_clientsession

    def create_with_cookie_jar(*args, **kwargs):
        session = create_session(*args, **kwargs)
        if (jar := kwargs.get("cookie_jar")) is not None:
            session._cookie_jar = jar
        return session

    monkeypatch.setattr(
        aiohttp_client, "_async_create_clientsession", create_with_cookie_jar
    )
    image = b"\xff\xd8\xffsynthetic-captcha"
    aioclient_mock.get(
        CAPTCHA_URL, content=image, headers={"Content-Type": "image/jpeg"}
    )
    form = await start_flow(hass, "desktop_start")
    assert aioclient_mock.call_count == 1
    assert form["step_id"] == "desktop_start"
    flow = hass.config_entries.flow._progress[form["flow_id"]]
    # The HTTP mock bypasses the actual response's Set-Cookie processing.
    flow._desktop_jar.update_cookies(
        SimpleCookie("JSESSIONID=example; Path=/contact-web"), URL(CAPTCHA_URL)
    )
    path = form["description_placeholders"]["image"]
    request = MockRequest(b"", mock_source="test", remote="127.0.0.1")
    response = await webhook.async_handle_webhook(hass, path.rsplit("/", 1)[1], request)
    assert response.content_type == "image/svg+xml"
    assert response.headers["Cache-Control"] == "no-store"
    assert f"data:image/jpeg;base64,{b64encode(image).decode()}" in response.text
    assert ">02:00<" in response.text
    assert 'begin="120s"' in response.text
    freezer.tick(timedelta(seconds=15))
    response = await webhook.async_handle_webhook(hass, path.rsplit("/", 1)[1], request)
    assert ">02:00<" not in response.text
    assert ">01:45<" in response.text

    data = {"name": "Desktop", "account": "19200000000", "consent": True}
    invalid = await submit(hass, form, {**data, "image_code": "!"})
    assert invalid["errors"] == {"image_code": "invalid_image_code"}
    assert aioclient_mock.call_count == 1

    freezer.tick(timedelta(seconds=106))
    form = await submit(hass, form, {**data, "image_code": "old7"})
    assert form["step_id"] == "desktop_start"
    new_path = form["description_placeholders"]["image"]
    assert new_path != path
    assert aioclient_mock.call_count == 2
    flow._desktop_jar.update_cookies(
        SimpleCookie("JSESSIONID=example; Path=/contact-web"), URL(CAPTCHA_URL)
    )

    api = f"{ORIGIN}/contact-web/api"
    headers = {"content-type": "application/json"}
    aioclient_mock.post(
        f"{api}/login/getVerifyCode",
        json={"status": "000000", "data": {}},
        headers=headers,
    )
    form = await submit(hass, form, {**data, "image_code": "aBc7"})
    assert form["step_id"] == "h5_code"
    assert aioclient_mock.call_count == 3
    expired = await webhook.async_handle_webhook(
        hass, new_path.rsplit("/", 1)[1], request
    )
    assert not expired.body

    aioclient_mock.post(
        f"{api}/login/gwLogin",
        json={"status": "000000", "data": {"sessionId": "desktop-session"}},
        headers=headers,
    )
    aioclient_mock.post(
        f"{api}/busi/qryBalanceFee",
        json={
            "status": "000000",
            "ok": True,
            "data": {"intfResultBean": {"balance": "1234"}},
        },
        headers=headers,
    )
    mock_resources(aioclient_mock, f"{api}/busi/qryBalanceFee")
    result = await submit(hass, form, {"code": "123456"})
    assert result["type"] is FlowResultType.CREATE_ENTRY, result.get("errors")
    entry = result["result"]
    assert state(hass, entry, "balance") == "12.34"
    assert entry.data["cookie"] == "JSESSIONID=example"
    assert "image_code" not in entry.data
    assert "code" not in entry.data


async def test_desktop_browser_verification_does_not_send_sms(hass, aioclient_mock):
    """A verification HTML page must not be exposed as a CAPTCHA."""
    from custom_components.china_broadnet.captcha import CAPTCHA_URL

    aioclient_mock.get(
        CAPTCHA_URL,
        text="<html>Challenge</html>",
        headers={"Content-Type": "text/html"},
    )
    form = await start_flow(hass, "desktop_start")
    assert form["step_id"] == "desktop_retry"
    assert form["errors"] == {"base": "captcha_challenge"}
    assert aioclient_mock.call_count == 1


async def test_portal_repairs_survive_outage_and_clear_on_reselection(
    hass, aioclient_mock, freezer
):
    """Only successful resource responses can create or clear a package warning."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.china_broadnet.portal import H5_ORIGIN
    from homeassistant.helpers import issue_registry as ir

    freezer.move_to("2026-11-15 00:00:00")
    url = f"{H5_ORIGIN}/contact-web/api/busi/qryBillInfo"
    row = {
        "busiType": "5",
        "highFee": "1048576",
        "balance": "524288",
        "startTime": "2026-11-01 00:00:00",
        "endTime": "2026-12-01 00:00:00",
        "discntName": "示例通用甲",
    }
    aioclient_mock.post(
        url,
        headers={"content-type": "application/json"},
        json={"status": "000000", "data": {"balance": "5.00"}},
    )
    mock_resources(aioclient_mock, url, [row])
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Example",
        data={
            "source": "portal",
            "portal_mode": "h5",
            "account": "19200000000",
            "session": "example-session",
            "packages": ["示例通用甲", "示例通用乙"],
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = ir.async_get(hass)
    issue_id = f"selected_packages_{entry.entry_id}"
    issue = registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == "selected_packages"
    assert state(hass, entry, "query_status") == "partial"
    assert state(hass, entry, "data_total") == STATE_UNAVAILABLE

    aioclient_mock.clear_requests()
    aioclient_mock.post(
        url,
        headers={"content-type": "application/json"},
        json={"status": "000000", "data": {"balance": "0"}},
    )
    aioclient_mock.post(url.rsplit("/", 1)[0] + "/qryUserRes", status=503)
    await entry.runtime_data.async_refresh()
    assert registry.async_get_issue(DOMAIN, issue_id) is issue
    assert state(hass, entry, "balance") == "0.0"

    aioclient_mock.clear_requests()
    aioclient_mock.post(
        url,
        headers={"content-type": "application/json"},
        json={"status": "000000", "data": {"balance": "0"}},
    )
    mock_resources(aioclient_mock, url, [row, {**row, "discntName": "示例通用乙"}])
    await entry.runtime_data.async_refresh()
    assert registry.async_get_issue(DOMAIN, issue_id) is None
    assert state(hass, entry, "query_status") == "success"

    aioclient_mock.clear_requests()
    aioclient_mock.post(
        url,
        headers={"content-type": "application/json"},
        json={"status": "000000", "data": {"balance": "0"}},
    )
    mock_resources(aioclient_mock, url, [row])
    await entry.runtime_data.async_refresh()
    assert registry.async_get_issue(DOMAIN, issue_id) is not None

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get_issue(DOMAIN, issue_id) is not None
    flow = await reconfigure(hass, entry)
    result = await submit(
        hass, flow, {"refresh_minutes": 15, "packages": ["示例通用甲"]}
    )
    assert result["reason"] == "reconfigure_successful"
    assert registry.async_get_issue(DOMAIN, issue_id) is None
    assert entry.runtime_data.update_interval == timedelta(minutes=15)
    entity_ids = {
        entity.entity_id
        for entity in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
    }
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entity_ids == {
        entity.entity_id
        for entity in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
    }
    assert all("/login/" not in str(call[1]) for call in aioclient_mock.mock_calls)

    # Removing the account must also remove a persistent warning.
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "packages": ["missing"]}
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get_issue(DOMAIN, issue_id) is not None
    await hass.config_entries.async_remove(entry.entry_id)
    assert registry.async_get_issue(DOMAIN, issue_id) is None


async def test_portal_diagnostics_and_recovery(hass, aioclient_mock, freezer):
    """Preserve success times across partial failure and report real backoff."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.china_broadnet.diagnostics import (
        async_get_config_entry_diagnostics,
    )
    from custom_components.china_broadnet.portal import H5_ORIGIN

    freezer.move_to("2026-11-15 00:00:00")
    url = f"{H5_ORIGIN}/contact-web/api/busi/qryBillInfo"
    aioclient_mock.post(
        url,
        headers={"content-type": "application/json"},
        json={"status": "000000", "data": {"balance": "9.00"}},
    )
    mock_resources(aioclient_mock, url)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Private alias",
        data={
            "source": "portal",
            "portal_mode": "h5",
            "account": "19200000000",
            "session": "example-private-session",
            "refresh_minutes": 15,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    first = await async_get_config_entry_diagnostics(hass, entry)
    assert isinstance(first["integration_version"], str)
    assert first["integration_version"]
    assert first["refresh_interval_minutes"] == 15
    assert first["verification_retry_after_seconds"] == 0
    assert first["last_successful_queries"]["balance"] is not None

    aioclient_mock.clear_requests()
    aioclient_mock.post(
        url,
        headers={"content-type": "application/json"},
        json={"status": "000000", "data": {"balance": "0"}},
    )
    aioclient_mock.post(url.rsplit("/", 1)[0] + "/qryUserRes", status=503)
    freezer.tick(timedelta(minutes=1))
    await entry.runtime_data.async_refresh()
    partial = await async_get_config_entry_diagnostics(hass, entry)
    assert (
        partial["last_successful_queries"]["resources"]
        == (first["last_successful_queries"]["resources"])
    )
    assert (
        partial["last_successful_queries"]["balance"]
        != (first["last_successful_queries"]["balance"])
    )

    aioclient_mock.clear_requests()
    aioclient_mock.post(
        url, text="<html>Verification</html>", headers={"content-type": "text/html"}
    )
    await entry.runtime_data.async_refresh()
    report = await async_get_config_entry_diagnostics(hass, entry)
    assert report["query_status"] == "verification"
    assert 3599 <= report["verification_retry_after_seconds"] <= 3600
    assert report["last_successful_queries"] == partial["last_successful_queries"]
    for secret in ("19200000000", "example-private-session", "Private alias", "9.00"):
        assert secret not in repr(report)
    assert state(hass, entry, "balance") == STATE_UNAVAILABLE
    await entry.runtime_data.async_manual_refresh()
    assert aioclient_mock.call_count == 1
    freezer.tick(timedelta(minutes=10))
    report = await async_get_config_entry_diagnostics(hass, entry)
    assert 2999 <= report["verification_retry_after_seconds"] <= 3000

    aioclient_mock.clear_requests()
    aioclient_mock.post(url, status=503, headers={"content-type": "application/json"})
    freezer.tick(timedelta(minutes=50, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert state(hass, entry, "query_status") == "connection_failed"
    assert (
        state(hass, entry, "last_updated")
        == partial["last_successful_queries"]["overall"]
    )

    aioclient_mock.clear_requests()
    aioclient_mock.post(
        url,
        headers={"content-type": "application/json"},
        json={"status": "000000", "data": {"balance": "0"}},
    )
    mock_resources(aioclient_mock, url)
    freezer.tick(timedelta(minutes=15, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert state(hass, entry, "query_status") == "success"
    assert state(hass, entry, "balance") == "0.0"
    recovered = await async_get_config_entry_diagnostics(hass, entry)
    assert recovered["verification_retry_after_seconds"] == 0
    assert (
        recovered["last_successful_queries"]["resources"]
        != (first["last_successful_queries"]["resources"])
    )
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_portal_month_rollover_and_saved_entry_startup(
    hass, aioclient_mock, freezer
):
    """Restore a saved entry after an offline month boundary without a login."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.china_broadnet.portal import H5_ORIGIN
    from homeassistant.helpers import issue_registry as ir

    freezer.move_to("2026-11-30 15:59:00")
    url = f"{H5_ORIGIN}/contact-web/api/busi/qryBillInfo"
    row = {
        "busiType": "5",
        "highFee": "1048576",
        "balance": "524288",
        "startTime": "2026-11-01 00:00:00",
        "endTime": "2026-12-01 00:00:00",
        "discntName": "示例通用旧包",
    }
    aioclient_mock.post(
        url,
        headers={"content-type": "application/json"},
        json={"status": "000000", "data": {"balance": "5"}},
    )
    mock_resources(aioclient_mock, url, [row, {**row, "discntName": "示例定向赠送"}])
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Saved account",
        data={
            "source": "portal",
            "portal_mode": "h5",
            "account": "19200000000",
            "session": "example-session",
            "refresh_minutes": 15,
            "packages": ["示例通用旧包"],
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    prior_used = float(state(hass, entry, "data_used"))
    directed_id = next(
        key
        for key, pack in entry.runtime_data.data["packages"].items()
        if pack["category"] == "directed"
    )
    assert float(state(hass, entry, f"package_{directed_id}")) > 0
    old_coordinator = entry.runtime_data
    snapshot_entity = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_monthly_snapshots"
    )

    aioclient_mock.clear_requests()
    aioclient_mock.post(url, status=503, headers={"content-type": "application/json"})
    freezer.move_to("2026-11-30 16:00:01")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert state(hass, entry, "monthly_snapshots") == "2026-11"
    assert state(hass, entry, "data_total") == STATE_UNAVAILABLE
    assert state(hass, entry, "data_used") == STATE_UNAVAILABLE
    assert state(hass, entry, f"package_{directed_id}") == STATE_UNAVAILABLE
    await entry.runtime_data.async_refresh()
    assert state(hass, entry, "balance") == STATE_UNAVAILABLE

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert old_coordinator._cancel_deadline is None
    # Exercise HA's startup deserializer with the actual persisted entry format.
    await hass.config_entries._store.async_save({"entries": [entry.as_dict()]})
    await hass.config_entries.async_initialize()
    restored = hass.config_entries.async_get_entry(entry.entry_id)
    assert restored is not entry
    assert restored.data == entry.data

    aioclient_mock.clear_requests()
    aioclient_mock.post(
        url,
        headers={"content-type": "application/json"},
        json={"status": "000000", "data": {"balance": "0"}},
    )
    renewed = {
        **row,
        "discntName": "示例通用新包",
        "balance": "1048576",
        "startTime": "2026-12-01 00:00:00",
        "endTime": "2027-01-01 00:00:00",
    }
    mock_resources(aioclient_mock, url, [renewed])
    assert await hass.config_entries.async_setup(restored.entry_id)
    await hass.async_block_till_done()
    assert restored.runtime_data is not old_coordinator
    assert restored.runtime_data.update_interval == timedelta(minutes=15)
    assert state(hass, restored, "balance") == "0.0"
    assert state(hass, restored, "data_total") == STATE_UNAVAILABLE
    assert ir.async_get(hass).async_get_issue(
        DOMAIN, f"selected_packages_{restored.entry_id}"
    )
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)

    flow = await reconfigure(hass, restored)
    result = await submit(
        hass, flow, {"refresh_minutes": 15, "packages": ["示例通用新包"]}
    )
    assert result["reason"] == "reconfigure_successful"
    assert state(hass, restored, "data_used") == "0.0"
    assert state(hass, restored, "general_usage_percent") == "0.0"
    assert not ir.async_get(hass).async_get_issue(
        DOMAIN, f"selected_packages_{restored.entry_id}"
    )
    snapshots = hass.states.get(snapshot_entity).attributes["months"]
    assert len(snapshots) == 1
    assert snapshots[0]["month"] == "2026-11"
    assert snapshots[0]["readings"]["data_used"]["value"] == prior_used
    assert all("/login/" not in str(call[1]) for call in aioclient_mock.mock_calls)
