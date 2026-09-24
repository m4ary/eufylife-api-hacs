"""EufyLife API number entities."""

from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .cloud import EufyLifeCloudError, EufyLifeLightCloud, EufyLifeLightDevice
from .const import DOMAIN
from .models import EufyLifeConfigEntry


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EufyLifeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up cloud-discovered EufyLife numbers."""
    cloud = entry.runtime_data.light_cloud
    if cloud is not None:
        async_add_entities(
            EufyLifeLightSpeed(cloud, device) for device in cloud.devices.values()
        )


class EufyLifeLightSpeed(NumberEntity):
    """Control the speed of light effects."""

    _attr_has_entity_name = True
    _attr_translation_key = "effect_speed"
    _attr_native_min_value = 1
    _attr_native_max_value = 10
    _attr_native_step = 1

    def __init__(self, cloud: EufyLifeLightCloud, device: EufyLifeLightDevice) -> None:
        self._cloud = cloud
        self._device = device
        self._attr_unique_id = f"{device.serial}_speed"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.serial)},
        )

    @property
    def native_value(self) -> float | None:
        """Return the current effect speed."""
        return self._device.speed

    async def async_set_native_value(self, value: float) -> None:
        """Set the effect speed."""
        try:
            await self._cloud.async_set_effect(
                self._device.serial,
                speed=int(value),
            )
        except EufyLifeCloudError as err:
            from homeassistant.exceptions import HomeAssistantError
            raise HomeAssistantError(str(err)) from err

    @property
    def available(self) -> bool:
        """Return whether the cloud link and device are available."""
        return self._cloud.connected and self._device.online is not False

    async def async_added_to_hass(self) -> None:
        """Subscribe to state changes."""
        self._cloud.add_listener(self._device.serial, self.async_write_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from state changes."""
        self._cloud.remove_listener(self._device.serial, self.async_write_ha_state)
