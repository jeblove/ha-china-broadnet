"""Offer a rate-limited portal refresh from the account device."""

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    """Expose the button only for accounts that support polling."""
    if entry.data["source"] == "portal":
        async_add_entities([RefreshButton(entry)])


class RefreshButton(ButtonEntity):
    """Refresh the existing coordinator without bypassing its backoff."""

    _attr_has_entity_name = True
    _attr_translation_key = "refresh"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry):
        self._coordinator = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_refresh"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_press(self):
        """Request a fresh portal observation."""
        await self._coordinator.async_manual_refresh()
