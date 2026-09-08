"""E10 lights for EufyLife API."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
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
    """Set up cloud-discovered E10 lights."""
    cloud = entry.runtime_data.light_cloud
    if cloud is not None:
        async_add_entities(
            EufyLifeLight(cloud, device) for device in cloud.devices.values()
        )


class EufyLifeLight(LightEntity):
    """A Eufy E10 light string or lamp."""

    _attr_has_entity_name = True
    _attr_color_mode = ColorMode.RGB
    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_supported_features = LightEntityFeature.EFFECT
    # Power/brightness are reported; color/effect are last device-acknowledged values.
    _attr_assumed_state = True

    def __init__(self, cloud: EufyLifeLightCloud, device: EufyLifeLightDevice) -> None:
        self._cloud = cloud
        self._device = device
        self._attr_unique_id = device.serial
        self._attr_name = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.serial)},
            manufacturer="Eufy",
            model=device.model,
            name=device.name,
        )

    @property
    def is_on(self) -> bool | None:
        """Return the last device-confirmed power state."""
        return self._device.is_on

    @property
    def brightness(self) -> int | None:
        """Convert the device's 0–100 percentage to HA's 0–255 scale."""
        value = self._device.brightness
        return round(value * 255 / 100) if value is not None else None

    @property
    def available(self) -> bool:
        """Return whether the cloud link and device are available."""
        return self._cloud.connected and self._device.online is not False

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        return self._device.rgb_color

    @property
    def effect(self) -> str | None:
        return self._device.effect

    @property
    def effect_list(self) -> list[str]:
        return list(self._device.effects)

    async def async_added_to_hass(self) -> None:
        """Subscribe to MQTT-backed state changes."""
        await super().async_added_to_hass()
        self._cloud.add_listener(self._device.serial, self.async_write_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from state changes."""
        self._cloud.remove_listener(self._device.serial, self.async_write_ha_state)
        await super().async_will_remove_from_hass()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Set a device-acknowledged color/preset, then requested power/brightness."""
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        if ATTR_RGB_COLOR in kwargs or ATTR_EFFECT in kwargs:
            try:
                await self._cloud.async_set_effect(
                    self._device.serial,
                    kwargs.get(ATTR_RGB_COLOR),
                    kwargs.get(ATTR_EFFECT),
                )
            except EufyLifeCloudError as err:
                raise HomeAssistantError(str(err)) from err
        self._set_power(
            True, round(brightness * 100 / 255) if brightness is not None else None
        )

    async def async_turn_off(self, **_kwargs: Any) -> None:
        """Ask the device to turn off."""
        self._set_power(False)

    def _set_power(self, is_on: bool, brightness: int | None = None) -> None:
        try:
            self._cloud.set_power(self._device.serial, is_on, brightness)
        except EufyLifeCloudError as err:
            raise HomeAssistantError(str(err)) from err
