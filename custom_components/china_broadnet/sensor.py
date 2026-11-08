"""Expose normalized usage as native Home Assistant sensors."""

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, TRAFFIC_KEYS
from .coordinator import BroadnetEntry, UsageCoordinator

SENSORS = (
    SensorEntityDescription(
        key="balance",
        translation_key="balance",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="CNY",
    ),
    *(
        SensorEntityDescription(
            key=key,
            translation_key=key,
            device_class=SensorDeviceClass.DATA_SIZE,
            native_unit_of_measurement=UnitOfInformation.GIGABYTES,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=3,
        )
        for key in TRAFFIC_KEYS
    ),
    SensorEntityDescription(
        key="last_updated",
        translation_key="last_updated",
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BroadnetEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create sensors once per config entry."""
    async_add_entities(BroadnetSensor(entry, description) for description in SENSORS)


class BroadnetSensor(CoordinatorEntity[UsageCoordinator], SensorEntity):
    """A measurement from a subscriber's shared usage data."""

    _attr_has_entity_name = True

    def __init__(self, entry: BroadnetEntry, description: SensorEntityDescription):
        super().__init__(entry.runtime_data)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self):
        """Return the current measurement or an unknown state."""
        return self.coordinator.data.get(self.entity_description.key)

    @property
    def available(self) -> bool:
        """Treat missing or expired readings as unavailable."""
        return super().available and self.native_value is not None
