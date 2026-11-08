"""UI setup and reconfiguration for China Broadnet."""

from datetime import UTC, datetime

import voluptuous as vol
from aiohttp import DummyCookieJar

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigFlow
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import DOMAIN, TRAFFIC_KEYS
from .portal import PortalAuthError, PortalClient, PortalError
from .sms import compile_template
from .usage import amount, group_for, make_report

MANUAL_KEYS = ("balance", *TRAFFIC_KEYS)
TEMPLATE_KEYS = ("balance_template", "traffic_template")


class ChinaBroadnetConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure a local source for a subscriber."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Choose how to supply readings."""
        return self.async_show_menu(
            step_id="user", menu_options=["portal", "manual", "sms"]
        )

    async def async_step_portal(self, user_input=None):
        """Connect an authorized official website session."""
        return await self._async_portal("portal", user_input)

    async def async_step_reauth(self, _entry_data):
        """Start replacement of an expired website session."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Validate a new session for the same account."""
        return await self._async_portal("reauth_confirm", user_input)

    async def async_step_portal_update(self, user_input=None):
        """Update the website session or selected traffic package."""
        return await self._async_portal("portal_update", user_input)

    async def _async_portal(self, step_id, user_input):
        entry = None
        if step_id == "reauth_confirm":
            entry = self._get_reauth_entry()
        elif step_id == "portal_update":
            entry = self._get_reconfigure_entry()
        package = entry.data.get("package", "") if entry else ""
        fields = {} if entry else {vol.Required("name"): TextSelector()}
        for key in ("session", "cookie"):
            fields[vol.Required(key)] = TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            )
        fields[vol.Required("user_agent")] = TextSelector()
        fields[vol.Optional("package", default=package)] = TextSelector()
        errors = {}
        if user_input is not None:
            settings = {
                key: user_input[key]
                for key in ("session", "cookie", "user_agent", "package")
                if key in user_input
            }
            http = async_create_clientsession(
                self.hass, auto_cleanup=False, cookie_jar=DummyCookieJar()
            )
            try:
                client = PortalClient(http, settings)
                if entry and client.account != PortalClient(http, entry.data).account:
                    return self.async_abort(reason="wrong_account")
                await client.async_balance()
            except ValueError, TypeError, RecursionError, KeyError:
                errors["base"] = "invalid_portal_session"
            except PortalAuthError:
                errors["base"] = "portal_auth_failed"
            except PortalError:
                errors["base"] = "cannot_connect"
            else:
                if entry:
                    return self.async_update_reload_and_abort(
                        entry, data_updates=settings
                    )
                return await self._async_create(
                    user_input["name"], {"source": "portal", **settings}
                )
            finally:
                http.detach()
        return self.async_show_form(
            step_id=step_id, data_schema=vol.Schema(fields), errors=errors
        )

    async def async_step_manual(self, user_input=None):
        """Set up manual observations from the official hall."""
        return await self._async_manual("manual", user_input)

    async def async_step_sms(self, user_input=None):
        """Set up the local SMS receiver."""
        return await self._async_sms("sms", user_input)

    async def async_step_reconfigure(self, user_input=None):
        """Update a reading or edit SMS settings."""
        source = self._get_reconfigure_entry().data["source"]
        if source == "portal":
            return await self.async_step_portal_update(user_input)
        if source == "manual":
            return await self._async_manual("manual_update", user_input)
        return await self._async_sms("sms_update", user_input)

    async def async_step_manual_update(self, user_input=None):
        """Save a fresh observation."""
        return await self._async_manual("manual_update", user_input)

    async def async_step_sms_update(self, user_input=None):
        """Update templates or rotate the webhook secret."""
        return await self._async_sms("sms_update", user_input)

    async def _async_manual(self, step_id, user_input):
        updating = step_id == "manual_update"
        fields = {} if updating else {vol.Required("name"): TextSelector()}
        fields.update({vol.Optional(key): TextSelector() for key in MANUAL_KEYS})
        errors = {}
        if user_input is not None:
            now = datetime.now(UTC)
            groups: dict[str, dict[str, float]] = {}
            for key in MANUAL_KEYS:
                if value := user_input.get(key, "").strip():
                    try:
                        parsed = amount(value, signed=key == "balance")
                    except ValueError:
                        errors[key] = "invalid_amount"
                    else:
                        groups.setdefault(group_for(key), {})[key] = float(parsed)

            if updating and not groups and not errors:
                errors["base"] = "missing_values"
            if not errors:
                reports = {
                    group: make_report(values, now, now)
                    for group, values in groups.items()
                }
                if updating:
                    entry = self._get_reconfigure_entry()
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={"reports": {**entry.data["reports"], **reports}},
                    )
                return await self._async_create(
                    user_input["name"], {"source": "manual", "reports": reports}
                )

        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(fields), user_input
            ),
            errors=errors,
        )

    async def _async_sms(self, step_id, user_input):
        updating = step_id == "sms_update"
        entry = self._get_reconfigure_entry() if updating else None
        fields = {} if updating else {vol.Required("name"): TextSelector()}
        fields[vol.Required("sender")] = TextSelector()
        for key in TEMPLATE_KEYS:
            fields[vol.Optional(key)] = TextSelector(TextSelectorConfig(multiline=True))
        fields[vol.Required("unit_base", default="1000")] = SelectSelector(
            SelectSelectorConfig(
                options=[
                    SelectOptionDict(value="1000", label="1000 (SI)"),
                    SelectOptionDict(value="1024", label="1024 (IEC)"),
                ]
            )
        )
        if updating:
            fields[vol.Optional("rotate", default=False)] = bool

        errors = {}
        if user_input is not None:
            sender = user_input["sender"].strip()
            if not 1 <= len(sender) <= 64:
                errors["sender"] = "invalid_sender"
            settings = {"sender": sender, "unit_base": user_input["unit_base"]}
            for key in TEMPLATE_KEYS:
                template = user_input.get(key, "").strip()
                settings[key] = template
                if template:
                    try:
                        compile_template(template, key.removesuffix("_template"))
                    except ValueError:
                        errors[key] = "invalid_template"
            if not any(settings[key] for key in TEMPLATE_KEYS):
                errors["base"] = "missing_template"
            if not errors:
                if entry is not None:
                    if any(entry.data[key] != value for key, value in settings.items()):
                        settings["reports"] = {}
                    if user_input.get("rotate"):
                        settings["webhook_id"] = webhook.async_generate_id()
                    return self.async_update_reload_and_abort(
                        entry, data_updates=settings
                    )
                return await self._async_create(
                    user_input["name"],
                    {
                        **settings,
                        "source": "sms",
                        "webhook_id": webhook.async_generate_id(),
                        "reports": {},
                    },
                )

        placeholders = (
            {"path": webhook.async_generate_path(entry.data["webhook_id"])}
            if entry is not None
            else None
        )
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(fields), user_input or (entry.data if entry else None)
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def _async_create(self, name, data):
        """Use a user-selected alias instead of storing a subscriber phone number."""
        name = name.strip()
        if not 1 <= len(name) <= 64:
            return self.async_abort(reason="invalid_name")
        await self.async_set_unique_id(name.casefold())
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=name, data=data)
