"""UI setup and reconfiguration for China Broadnet."""

import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from time import monotonic

import voluptuous as vol
from aiohttp import CookieJar, DummyCookieJar

from homeassistant.components import webhook
from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlow
from homeassistant.helpers.aiohttp_client import (
    async_create_clientsession,
    async_get_clientsession,
)
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.setup import async_setup_component

from .captcha import (
    LOGIN_PAGE,
    async_desktop_login,
    async_desktop_send_sms,
    async_fetch_captcha,
    async_publish_captcha,
)
from .const import DOMAIN, TRAFFIC_KEYS
from .portal import (
    H5_ORIGIN,
    PortalAuthError,
    PortalChallengeError,
    PortalClient,
    PortalCodeError,
    PortalCurlError,
    PortalError,
    async_h5_login,
    async_h5_send_sms,
    parse_portal_curl,
    valid_phone,
)
from .sms import compile_template
from .usage import amount, group_for, make_report

MANUAL_KEYS = ("balance", *TRAFFIC_KEYS)
TEMPLATE_KEYS = ("balance_template", "traffic_template")


class ChinaBroadnetConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure a local source for a subscriber."""

    VERSION = 1

    def __init__(self) -> None:
        """Keep pending login state in this configuration flow only."""
        super().__init__()
        self._login_data: dict | None = None
        self._login_name = ""
        self._sms_at = float("-inf")
        self._desktop_jar: CookieJar | None = None
        self._image_path = ""
        self._image_until = 0.0
        self._image_code = ""
        self._remove_image = None

    async def async_step_user(self, user_input=None):
        """Choose how to supply readings."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["h5_start", "desktop_start", "portal", "manual", "sms"],
        )

    async def async_step_h5_start(self, user_input=None):
        """Send a login SMS only after the user submits the phone form."""
        return await self._async_login_start("h5_start", user_input)

    async def async_step_desktop_start(self, user_input=None):
        """Offer a desktop login only if the carrier returns a CAPTCHA image."""
        suggested = None
        if not self._image_path or monotonic() >= self._image_until:
            self._desktop_jar = CookieJar()
            try:
                async with self._async_desktop_http() as http:
                    mime, image = await async_fetch_captcha(http)
            except PortalChallengeError:
                error = "captcha_challenge"
            except PortalError:
                error = "cannot_connect"
            else:
                if await async_setup_component(self.hass, webhook.DOMAIN, {}):
                    error = None
                    self._image_path, self._image_until, self._remove_image = (
                        async_publish_captcha(self.hass, mime, image)
                    )
                    if user_input:
                        suggested = {
                            key: value
                            for key, value in user_input.items()
                            if key != "image_code"
                        }
                    user_input = None
                else:
                    error = "cannot_connect"
            if error:
                return self.async_show_form(
                    step_id="desktop_retry",
                    data_schema=vol.Schema({}),
                    errors={"base": error},
                )
        return await self._async_login_start("desktop_start", user_input, suggested)

    async def async_step_desktop_retry(self, _user_input=None):
        """Retry fetching the image without sending a login SMS."""
        return await self.async_step_desktop_start()

    @asynccontextmanager
    async def _async_desktop_http(self):
        http = async_create_clientsession(
            self.hass, auto_cleanup=False, cookie_jar=self._desktop_jar
        )
        try:
            yield http
        finally:
            http.detach()

    async def _async_login_start(self, step_id, user_input, suggested=None):
        desktop = step_id == "desktop_start"
        entry = self._get_reauth_entry() if self.source == SOURCE_REAUTH else None
        fields = {}
        if not entry:
            fields = {
                vol.Required("name"): TextSelector(),
                vol.Required("account"): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEL)
                ),
                vol.Optional("package", default=""): TextSelector(),
            }
        fields[vol.Required("consent", default=False)] = bool
        if desktop:
            fields[vol.Optional("image_code")] = TextSelector()
        errors = {}
        if user_input is not None:
            account = entry.data["account"] if entry else user_input["account"].strip()
            if not valid_phone(account):
                errors["account"] = "invalid_phone"
            elif not user_input["consent"]:
                errors["consent"] = "login_consent"
            elif desktop and not re.fullmatch(
                r"[A-Za-z0-9]{4,8}", user_input.get("image_code", "").strip()
            ):
                errors["image_code"] = "invalid_image_code"
            else:
                package = (
                    entry.data.get("package", "")
                    if entry
                    else user_input.get("package", "").strip()
                )
                name = entry.title if entry else user_input["name"].strip()
                if not 1 <= len(name) <= 64:
                    return self.async_abort(reason="invalid_name")
                if len(package) > 128:
                    errors["package"] = "invalid_package"
                elif not entry and any(
                    item.data.get("account") == account
                    for item in self._async_current_entries()
                ):
                    return self.async_abort(reason="already_configured")
                else:
                    if not entry:
                        await self.async_set_unique_id(name.casefold())
                        self._abort_if_unique_id_configured()
                    error = await self._async_send_login_sms(account, desktop)
                    if error is None:
                        self._login_name = name
                        self._login_data = {
                            "source": "portal",
                            "portal_mode": "desktop" if desktop else "h5",
                            "account": account,
                            "package": package,
                        }
                        if desktop:
                            self._image_code = user_input["image_code"].strip()
                            if self._remove_image:
                                self._remove_image()
                        return await self.async_step_h5_code()
                    errors["base"] = error
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(fields), suggested
            ),
            errors=errors,
            description_placeholders=(
                {"image": self._image_path, "website": LOGIN_PAGE}
                if desktop
                else {"website": f"{H5_ORIGIN}/h5-app/"}
            ),
        )

    async def _async_send_login_sms(self, account, desktop):
        if monotonic() - self._sms_at < 60:
            return "sms_cooldown"
        self._sms_at = monotonic()
        try:
            if desktop:
                async with self._async_desktop_http() as http:
                    await async_desktop_send_sms(http, account)
            else:
                await async_h5_send_sms(async_get_clientsession(self.hass), account)
        except PortalCodeError:
            return "sms_rejected"
        except PortalChallengeError:
            return "portal_challenge"
        except PortalError:
            return "cannot_connect"
        return None

    async def async_step_h5_code(self, user_input=None):
        """Verify the login code and check an authenticated balance."""
        if self._login_data is None:
            return await self.async_step_h5_start()
        settings = self._login_data
        desktop = settings["portal_mode"] == "desktop"
        errors = {}
        if user_input is not None:
            code = user_input.get("code", "").strip()
            if user_input.get("resend") and "session" not in settings:
                if error := await self._async_send_login_sms(
                    settings["account"], desktop
                ):
                    errors["base"] = error
            elif "session" not in settings and not re.fullmatch(r"[0-9]{4,8}", code):
                errors["code"] = "invalid_sms_code"
            else:
                http = async_get_clientsession(self.hass)
                try:
                    if "session" not in settings:
                        if desktop:
                            async with self._async_desktop_http() as desktop_http:
                                settings.update(
                                    await async_desktop_login(
                                        desktop_http,
                                        settings["account"],
                                        code,
                                        self._image_code,
                                    )
                                )
                        else:
                            settings["session"] = await async_h5_login(
                                http, settings["account"], code
                            )
                    client = PortalClient(http, settings)
                    await client.async_balance()
                    if client.cookie is not None:
                        settings["cookie"] = client.cookie
                except PortalCodeError:
                    if desktop:
                        errors["base"] = "invalid_desktop_codes"
                    else:
                        errors["code"] = "invalid_sms_code"
                except PortalAuthError:
                    settings.pop("session", None)
                    errors["base"] = "portal_auth_failed"
                except PortalChallengeError:
                    errors["base"] = "portal_challenge"
                except PortalError:
                    errors["base"] = "cannot_connect"
                else:
                    if self.source == SOURCE_REAUTH:
                        return self.async_update_reload_and_abort(
                            self._get_reauth_entry(), data_updates=settings
                        )
                    return await self._async_create(self._login_name, settings)
        fields = {}
        if "session" not in settings:
            fields = {
                vol.Optional("code"): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
                vol.Optional("resend", default=False): bool,
            }
        return self.async_show_form(
            step_id="h5_code", data_schema=vol.Schema(fields), errors=errors
        )

    async def async_step_portal(self, user_input=None):
        """Connect an authorized official website session."""
        return await self._async_portal("portal", user_input)

    async def async_step_reauth(self, _entry_data):
        """Start replacement of an expired website session."""
        mode = self._get_reauth_entry().data.get("portal_mode")
        if mode == "desktop":
            return await self.async_step_desktop_start()
        if mode == "h5":
            return await self.async_step_h5_start()
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
        fields[vol.Required("session")] = TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        )
        fields[vol.Required("curl")] = TextSelector(TextSelectorConfig(multiline=True))
        fields[vol.Optional("package", default=package)] = TextSelector()
        errors = {}
        if user_input is not None:
            settings = {
                key: user_input[key]
                for key in ("session", "package")
                if key in user_input
            }
            http = async_create_clientsession(
                self.hass, auto_cleanup=False, cookie_jar=DummyCookieJar()
            )
            try:
                settings.update(parse_portal_curl(user_input["curl"]))
                client = PortalClient(http, settings)
                if entry and client.account != PortalClient(http, entry.data).account:
                    return self.async_abort(reason="wrong_account")
                await client.async_balance()
            except PortalCurlError:
                errors["curl"] = "invalid_portal_curl"
            except ValueError, TypeError, RecursionError, KeyError:
                errors["base"] = "invalid_portal_session"
            except PortalChallengeError:
                errors["base"] = "portal_challenge"
            except PortalAuthError:
                errors["base"] = "portal_auth_failed"
            except PortalError:
                errors["base"] = "cannot_connect"
            else:
                settings["cookie"] = client.cookie
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
        entry = self._get_reconfigure_entry()
        source = entry.data["source"]
        if source == "portal":
            if entry.data.get("portal_mode") in ("h5", "desktop"):
                return await self.async_step_h5_update(user_input)
            return await self.async_step_portal_update(user_input)
        if source == "manual":
            return await self._async_manual("manual_update", user_input)
        return await self._async_sms("sms_update", user_input)

    async def async_step_h5_update(self, user_input=None):
        """Update the selected package without requesting a new login code."""
        entry = self._get_reconfigure_entry()
        errors = {}
        if user_input is not None:
            package = user_input.get("package", "").strip()
            if len(package) <= 128:
                return self.async_update_reload_and_abort(
                    entry, data_updates={"package": package}
                )
            errors["package"] = "invalid_package"
        return self.async_show_form(
            step_id="h5_update",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "package", default=entry.data.get("package", "")
                    ): TextSelector()
                }
            ),
            errors=errors,
        )

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
