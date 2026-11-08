"""Share usage data and retire old measurements across all sensors."""

from datetime import UTC, datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN
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

    async def _async_update_data(self) -> dict:
        return current_values(self.entry.data["reports"], datetime.now(UTC))

    @callback
    def async_accept_report(self, group: str, report: dict, now: datetime) -> None:
        """Persist normalized measurements and publish them to listeners."""
        reports = {**self.entry.data["reports"], group: report}
        self.hass.config_entries.async_update_entry(
            self.entry, data={**self.entry.data, "reports": reports}
        )
        self.async_set_updated_data(current_values(reports, now))
