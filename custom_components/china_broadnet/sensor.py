"""Expose normalized usage as native Home Assistant sensors."""

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfInformation, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, TRAFFIC_KEYS
from .coordinator import BroadnetEntry, UsageCoordinator
from .usage import traffic_gb

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


PORTAL_SENSORS = (
    SensorEntityDescription(
        key="monthly_spend",
        translation_key="monthly_spend",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="CNY",
    ),
    *(
        SensorEntityDescription(
            key=key,
            translation_key=key,
            device_class=SensorDeviceClass.MONETARY,
            native_unit_of_measurement="CNY",
        )
        for key in ("outside_data_fee", "previous_bill")
    ),
    SensorEntityDescription(key="plan_name", translation_key="plan_name"),
    SensorEntityDescription(
        key="voice_remaining",
        translation_key="voice_remaining",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
    ),
)


PORTAL_DIAGNOSTICS = (
    SensorEntityDescription(
        key="query_status",
        translation_key="query_status",
        device_class=SensorDeviceClass.ENUM,
        options=[
            "pending",
            "success",
            "partial",
            "auth_expired",
            "verification",
            "connection_failed",
        ],
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    *(
        SensorEntityDescription(
            key=key,
            translation_key=key,
            device_class=SensorDeviceClass.TIMESTAMP,
            entity_category=EntityCategory.DIAGNOSTIC,
        )
        for key in ("balance_updated", "resources_updated")
    ),
    SensorEntityDescription(
        key="monthly_snapshots", translation_key="monthly_snapshots"
    ),
    SensorEntityDescription(
        key="remaining_days",
        translation_key="remaining_days",
        native_unit_of_measurement=UnitOfTime.DAYS,
        suggested_display_precision=2,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BroadnetEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create sensors once per config entry."""
    async_add_entities(BroadnetSensor(entry, description) for description in SENSORS)
    if entry.data["source"] != "portal":
        return
    async_add_entities(
        BroadnetSensor(entry, description)
        for description in (*PORTAL_SENSORS, *PORTAL_DIAGNOSTICS)
    )
    coordinator = entry.runtime_data
    added = set()

    @callback
    def add_packages():
        entities = []
        for key, package in coordinator.data.get("packages", {}).items():
            if key in added or package["category"] == "general":
                continue
            added.add(key)
            entities.extend(
                PackageSensor(entry, key, package["name"], expiry=expiry)
                for expiry in (False, True)
            )
        if entities:
            async_add_entities(entities)

    entry.async_on_unload(coordinator.async_add_listener(add_packages))
    add_packages()


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
        key = self.entity_description.key
        if key in ("query_status", "balance_updated", "resources_updated"):
            return getattr(self.coordinator, key)
        if key == "monthly_snapshots":
            snapshots = self.coordinator.data.get(key, [])
            return snapshots[0]["month"] if snapshots else None
        return self.coordinator.data.get(key)

    @property
    def extra_state_attributes(self):
        """Preserve usage scope in Recorder and expose archived observations."""
        key = self.entity_description.key
        if key == "data_used" and (scope := self.coordinator.data.get("usage_scope")):
            return {"usage_scope": scope}
        if key == "previous_bill" and (
            cycle := self.coordinator.data.get("previous_bill_cycle")
        ):
            return {"billing_cycle": cycle}
        if key == "monthly_snapshots":
            return {"months": self.coordinator.data.get(key, [])}
        return None

    @property
    def available(self) -> bool:
        """Treat missing or expired readings as unavailable."""
        return self.native_value is not None and (
            self.entity_description.key
            in (
                "last_updated",
                "query_status",
                "balance_updated",
                "resources_updated",
                "monthly_snapshots",
            )
            or super().available
        )


class PackageSensor(BroadnetSensor):
    """Expose one classified allowance and its validity window."""

    def __init__(self, entry: BroadnetEntry, key: str, name: str, *, expiry: bool):
        super().__init__(
            entry,
            SensorEntityDescription(
                key=f"package_{key}_expiry" if expiry else f"package_{key}",
                translation_key="package_expiry" if expiry else "package_remaining",
                device_class=SensorDeviceClass.TIMESTAMP
                if expiry
                else SensorDeviceClass.DATA_SIZE,
                native_unit_of_measurement=None
                if expiry
                else UnitOfInformation.GIGABYTES,
                suggested_display_precision=None if expiry else 3,
            ),
        )
        self._package_id = key
        self._attr_translation_placeholders = {"package": name}
        self._expiry = expiry

    @property
    def _package(self):
        return self.coordinator.data.get("packages", {}).get(self._package_id)

    @property
    def native_value(self):
        """Return remaining decimal GB or the expiration timestamp."""
        if (package := self._package) is not None:
            if self._expiry:
                return package["end"]
            return traffic_gb(str(package["remaining"]), "KiB", 1024)
        return None

    @property
    def extra_state_attributes(self):
        """Expose validity and category without combining unrelated packages."""
        if (package := self._package) is None:
            return None
        return {
            "category": package["category"],
            "valid_from": package["start"].isoformat(),
            "valid_until": package["end"].isoformat(),
        }
