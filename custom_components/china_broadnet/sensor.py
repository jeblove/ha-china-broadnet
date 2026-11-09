"""Expose normalized usage as native Home Assistant sensors."""

from datetime import UTC, datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfInformation,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, TRAFFIC_KEYS
from .coordinator import BroadnetEntry, UsageCoordinator
from .usage import SHANGHAI, traffic_gb, usage_percent

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


TRAFFIC_RATIOS = (
    "general_usage_percent",
    "general_remaining_percent",
    "directed_usage_percent",
    "directed_remaining_percent",
)

PORTAL_SENSORS = (
    *(
        SensorEntityDescription(
            key=key,
            translation_key=key,
            native_unit_of_measurement=PERCENTAGE,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=2,
        )
        for key in TRAFFIC_RATIOS
    ),
    SensorEntityDescription(
        key="estimated_depletion",
        translation_key="estimated_depletion",
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
    SensorEntityDescription(
        key="data_pace",
        translation_key="data_pace",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=2,
    ),
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
        for key in (
            "outside_total_fee",
            "outside_data_fee",
            "outside_voice_fee",
            "outside_sms_fee",
            "previous_bill",
        )
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
            if package["category"] == "directed":
                entities.append(
                    PackageSensor(entry, key, package["name"], kind="usage_percent")
                )
            entities.extend(
                PackageSensor(entry, key, package["name"], kind=kind)
                for kind in ("remaining", "expiry")
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
        if key in (*TRAFFIC_RATIOS, "estimated_depletion"):
            data = self.coordinator.data
            month = datetime.now(UTC).astimezone(SHANGHAI).strftime("%Y-%m")
            if data.get("traffic_month") != month:
                return None
            if key in TRAFFIC_RATIOS:
                if key.startswith("general_"):
                    percent = usage_percent(
                        data.get("data_total"), data.get("data_remaining")
                    )
                else:
                    percent = data.get("directed_usage_percent")
                if percent is not None and key.endswith("_remaining_percent"):
                    return 100 - percent
                return percent
        if key == "data_pace":
            month = datetime.now(UTC).astimezone(SHANGHAI).strftime("%Y-%m")
            if self.coordinator.data.get("pace_month") != month:
                return None
        if key == "monthly_snapshots":
            snapshots = self.coordinator.data.get(key, [])
            return snapshots[0]["month"] if snapshots else None
        return self.coordinator.data.get(key)

    @property
    def extra_state_attributes(self):
        """Preserve usage scope in Recorder and expose archived observations."""
        key = self.entity_description.key
        if key == "query_status" and self.coordinator.query_status == "partial":
            return {
                "missing_sections": self.coordinator.data.get("missing_sections", [])
            }
        if key == "data_used" and (scope := self.coordinator.data.get("usage_scope")):
            return {"usage_scope": scope}
        if key == "previous_bill" and (
            cycle := self.coordinator.data.get("previous_bill_cycle")
        ):
            return {"billing_cycle": cycle}
        if key == "data_pace" and self.native_value is not None:
            return {
                "used_percent": self.coordinator.data["pace_used_percent"],
                "elapsed_percent": self.coordinator.data["pace_elapsed_percent"],
            }
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

    def __init__(self, entry: BroadnetEntry, key: str, name: str, *, kind: str):
        device_class, unit, precision = {
            "remaining": (SensorDeviceClass.DATA_SIZE, UnitOfInformation.GIGABYTES, 3),
            "expiry": (SensorDeviceClass.TIMESTAMP, None, None),
            "usage_percent": (None, PERCENTAGE, 2),
        }[kind]
        suffix = "" if kind == "remaining" else f"_{kind}"
        super().__init__(
            entry,
            SensorEntityDescription(
                key=f"package_{key}{suffix}",
                translation_key=f"package_{kind}",
                device_class=device_class,
                entity_registry_enabled_default=kind != "usage_percent",
                native_unit_of_measurement=unit,
                state_class=SensorStateClass.MEASUREMENT
                if kind == "usage_percent"
                else None,
                suggested_display_precision=precision,
            ),
        )
        self._package_id = key
        self._attr_translation_placeholders = {"package": name}
        self._kind = kind

    @property
    def _package(self):
        return self.coordinator.data.get("packages", {}).get(self._package_id)

    @property
    def native_value(self):
        """Return remaining decimal GB or the expiration timestamp."""
        if (package := self._package) is not None:
            if self._kind == "expiry":
                return package["end"]
            if self._kind == "usage_percent":
                if datetime.now(UTC) >= package["end"]:
                    return None
                return usage_percent(package["total"], package["remaining"])
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
