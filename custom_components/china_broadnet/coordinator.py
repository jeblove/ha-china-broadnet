"""Share usage data and retire old measurements across all sensors."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from aiohttp import DummyCookieJar

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .portal import PortalAuthError, PortalChallengeError, PortalClient, PortalError
from .usage import current_values, report_window

type BroadnetEntry = ConfigEntry[UsageCoordinator]


class UsageCoordinator(DataUpdateCoordinator[dict]):
    """Publish incoming reports and schedule their freshness transitions."""

    def __init__(self, hass: HomeAssistant, entry: BroadnetEntry) -> None:
        portal = entry.data["source"] == "portal"
        super().__init__(
            hass,
            logging.getLogger(__name__),
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=8) if portal else None,
            always_update=False,
        )
        self.entry = entry
        self._portal_data = entry.data if portal else None
        self._portal = (
            PortalClient(
                async_create_clientsession(hass, cookie_jar=DummyCookieJar()),
                entry.data,
            )
            if portal
            else None
        )
        self._cancel_deadline: Callable[[], None] | None = None
        entry.async_on_unload(self._async_cancel_deadline)

    async def _async_update_data(self) -> dict:
        if self._portal is not None:
            try:
                data = await self._portal.async_read()
            except PortalAuthError as err:
                raise ConfigEntryAuthFailed("Refresh the website session") from err
            except PortalChallengeError as err:
                raise UpdateFailed(str(err), retry_after=3600) from err
            except PortalError as err:
                raise UpdateFailed(str(err)) from err
            cookie = self._portal.cookie
            if (
                cookie is not None
                and self.entry.data is self._portal_data
                and self.entry.data["cookie"] != cookie
            ):
                self.hass.config_entries.async_update_entry(
                    self.entry, data={**self.entry.data, "cookie": cookie}
                )
                self._portal_data = self.entry.data
            return data
        return self._async_snapshot(datetime.now(UTC))

    @callback
    def _async_cancel_deadline(self) -> None:
        if self._cancel_deadline is not None:
            self._cancel_deadline()
            self._cancel_deadline = None

    @callback
    def _async_snapshot(self, now: datetime) -> dict:
        reports = self.entry.data["reports"]
        self._async_cancel_deadline()
        deadline = min(
            (
                point
                for group, report in reports.items()
                for point in report_window(
                    group, datetime.fromisoformat(report["observed"])
                )
                if point > now
            ),
            default=None,
        )
        if deadline is not None:
            self._cancel_deadline = async_track_point_in_utc_time(
                self.hass, self._async_deadline_reached, deadline
            )
        return current_values(reports, now)

    @callback
    def _async_deadline_reached(self, _deadline: datetime) -> None:
        self._cancel_deadline = None
        self.async_set_updated_data(self._async_snapshot(datetime.now(UTC)))

    @callback
    def async_accept_report(self, group: str, report: dict, now: datetime) -> None:
        """Persist normalized measurements and publish them to listeners."""
        reports = {**self.entry.data["reports"], group: report}
        self.hass.config_entries.async_update_entry(
            self.entry, data={**self.entry.data, "reports": reports}
        )
        self.async_set_updated_data(self._async_snapshot(now))
