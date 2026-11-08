"""Share usage data and retire old measurements across all sensors."""

import logging
from datetime import UTC, datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import BROWSER_MAX_AGE, DOMAIN, MAX_AGE
from .usage import current_values

type BroadnetEntry = ConfigEntry[UsageCoordinator]


class UsageCoordinator(DataUpdateCoordinator[dict]):
    """Publish incoming reports and periodically check their freshness."""

    def __init__(self, hass: HomeAssistant, entry: BroadnetEntry) -> None:
        super().__init__(
            hass,
            logging.getLogger(__name__),
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=5),
            always_update=False,
        )
        self.entry = entry
        self.max_age = BROWSER_MAX_AGE if entry.data["source"] == "browser" else MAX_AGE

    async def _async_update_data(self) -> dict:
        return current_values(
            self.entry.data["reports"], datetime.now(UTC), self.max_age
        )

    @callback
    def async_accept_reports(self, updates: dict, now: datetime) -> None:
        """Persist normalized measurements and publish them to listeners."""
        reports = {**self.entry.data["reports"], **updates}
        self.hass.config_entries.async_update_entry(
            self.entry, data={**self.entry.data, "reports": reports}
        )
        self.async_set_updated_data(current_values(reports, now, self.max_age))
