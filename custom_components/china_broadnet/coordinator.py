"""Share usage data and retire old measurements across all sensors."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from time import monotonic

from aiohttp import DummyCookieJar

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .insights import UsageInsights
from .portal import PortalAuthError, PortalChallengeError, PortalClient, PortalError
from .usage import SHANGHAI, current_values, report_window

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
        self.insights = UsageInsights(hass, entry.entry_id) if portal else None
        self.query_status = "pending"
        self._query_running = False
        self._next_manual_refresh = 0.0
        self.balance_updated = None
        self.resources_updated = None
        self._cancel_deadline: Callable[[], None] | None = None
        entry.async_on_unload(self._async_cancel_deadline)

    async def async_manual_refresh(self) -> None:
        """Ignore repeat presses and honor authentication and verification backoff."""
        if (
            self._portal is None
            or self._query_running
            or self.query_status == "auth_expired"
            or monotonic() < self._next_manual_refresh
        ):
            return
        self._next_manual_refresh = monotonic() + 60
        await self.async_refresh()

    async def _async_update_data(self) -> dict:
        if self._portal is not None:
            self._query_running = True
            self._next_manual_refresh = monotonic() + 60
            try:
                data = await self._portal.async_read()
            except PortalAuthError as err:
                self.query_status = "auth_expired"
                raise ConfigEntryAuthFailed("Refresh the website session") from err
            except PortalChallengeError as err:
                self.query_status = "verification"
                self._next_manual_refresh = monotonic() + 3600
                raise UpdateFailed(str(err), retry_after=3600) from err
            except PortalError as err:
                self.query_status = "connection_failed"
                raise UpdateFailed(str(err)) from err
            finally:
                self._query_running = False
            self.balance_updated = data["balance_at"]
            if data["resources_ok"]:
                self.resources_updated = data["resources_at"]
            self.query_status = "partial" if data["missing_sections"] else "success"
            await self.insights.async_apply(data, data["last_updated"])
            if self._cancel_deadline is None:
                self._async_schedule_month_end(datetime.now(UTC))
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
    def _async_schedule_month_end(self, now: datetime) -> None:
        local = now.astimezone(SHANGHAI)
        boundary = local.replace(
            year=local.year + (local.month == 12),
            month=local.month % 12 + 1,
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        self._cancel_deadline = async_track_point_in_utc_time(
            self.hass, self._async_month_ended, boundary.astimezone(UTC)
        )

    @callback
    def _async_month_ended(self, _deadline: datetime) -> None:
        self.entry.async_create_task(
            self.hass, self._async_archive_month(), "archive China Broadnet usage"
        )

    async def _async_archive_month(self) -> None:
        now = datetime.now(UTC)
        months = await self.insights.async_archive(now)
        if self.data is not None:
            self.data = {**self.data, "monthly_snapshots": months}
            self.async_update_listeners()
        self._async_schedule_month_end(now)

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
