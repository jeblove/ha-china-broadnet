"""Set up the China Broadnet integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import UsageCoordinator
from .receiver import async_register_receiver

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up sensors and, for SMS entries, the local webhook."""
    coordinator = UsageCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if entry.data["source"] == "sms":
        async_register_receiver(hass, entry)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload sensors and let the config entry unregister the webhook."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
