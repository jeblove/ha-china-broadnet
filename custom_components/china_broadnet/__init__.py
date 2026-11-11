"""Set up the China Broadnet integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .coordinator import UsageCoordinator
from .insights import snapshot_store
from .receiver import async_register_receiver

PLATFORMS = [Platform.SENSOR, Platform.BUTTON]


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


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove locally stored monthly snapshots with the account."""
    ir.async_delete_issue(hass, DOMAIN, f"selected_packages_{entry.entry_id}")
    if entry.data["source"] == "portal":
        await snapshot_store(hass, entry.entry_id).async_remove()
