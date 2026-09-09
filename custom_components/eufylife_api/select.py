"""EufyLife API select entities."""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .cloud import EufyLifeCloudError, EufyLifeLightCloud, EufyLifeLightDevice
from .const import DOMAIN
from .models import EufyLifeConfigEntry

DIRECTION_FORWARD = "Forward"
DIRECTION_BACKWARD = "Backward"
DIRECTIONS = {
    0: DIRECTION_FORWARD,
    1: DIRECTION_BACKWARD,
}
REVERSE_DIRECTIONS = {v: k for k, v in DIRECTIONS.items()}


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EufyLifeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up cloud-discovered EufyLife selects."""
    cloud = entry.runtime_data.light_cloud
    if cloud is not None:
        async_add_entities(
            EufyLifeLightDirection(cloud, device) for device in cloud.devices.values()
        )


class EufyLifeLightDirection(SelectEntity):
    """Control the direction of light effects."""

    _attr_has_entity_name = True
    _attr_translation_key = "effect_direction"
    _attr_options = list(DIRECTIONS.values())

    def __init__(self, cloud: EufyLifeLightCloud, device: EufyLifeLightDevice) -> None:
        self._cloud = cloud
        self._device = device
        self._attr_unique_id = f"{device.serial}_direction"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.serial)},
        )

    @property
    def current_option(self) -> str | None:
        """Return the current effect direction."""
        return DIRECTIONS.get(self._device.direction)

    async def async_select_option(self, option: str) -> None:
        """Set the effect direction."""
        try:
            await self._cloud.async_set_effect(
                self._device.serial,
                direction=REVERSE_DIRECTIONS[option],
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
