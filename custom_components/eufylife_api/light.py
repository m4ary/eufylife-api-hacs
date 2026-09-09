"""E10 lights for EufyLife API."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ATTR_RGBWW_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import entity_platform
import homeassistant.helpers.config_validation as cv
import voluptuous as vol

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
        entities: list[LightEntity] = []
        for device in cloud.devices.values():
            entities.append(EufyLifeLight(cloud, device))
            if device.lamp_count and device.lamp_count > 1:
                for i in range(device.lamp_count):
                    entities.append(EufyLifeSegmentLight(cloud, device, i))
        async_add_entities(entities)

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "set_light_settings",
        {
            vol.Optional("effect"): cv.string,
            vol.Optional("colors"): vol.All(cv.ensure_list, [vol.All(cv.ensure_list, [vol.Coerce(int)])]),
            vol.Optional("speed"): vol.All(vol.Coerce(int), vol.Range(min=1, max=10)),
            vol.Optional("direction"): vol.All(vol.Coerce(int), vol.Range(min=0, max=1)),
        },
        "async_set_light_settings",
    )
    platform.async_register_entity_service(
        "set_light_show",
        {
            vol.Required("params"): cv.string,
            vol.Optional("use_ai_opcode"): cv.boolean,
        },
        "async_set_light_show",
    )


class EufyLifeLight(LightEntity):
    """A Eufy E10 light string or lamp."""

    _attr_has_entity_name = True
    _attr_color_mode = ColorMode.RGBWW
    _attr_supported_color_modes = {ColorMode.RGBWW}
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
        if self._device.colors and len(self._device.colors) > 0:
            return self._device.colors[0][:3]
        return self._device.rgb_color

    @property
    def rgbww_color(self) -> tuple[int, int, int, int, int] | None:
        if self._device.colors and len(self._device.colors) > 0:
            color = self._device.colors[0]
            if len(color) == 5:
                return color
            return (*color, 0, 0)
        return self._device.rgbww_color

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return device-specific state attributes."""
        return {
            "speed": self._device.speed,
            "direction": self._device.direction,
            "lamp_count": self._device.lamp_count,
            "model": self._device.model,
        }

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
        if ATTR_RGB_COLOR in kwargs or ATTR_RGBWW_COLOR in kwargs or ATTR_EFFECT in kwargs:
            try:
                await self._cloud.async_set_effect(
                    self._device.serial,
                    rgb_color=kwargs.get(ATTR_RGB_COLOR),
                    rgbww_color=kwargs.get(ATTR_RGBWW_COLOR),
                    effect=kwargs.get(ATTR_EFFECT),
                )
            except EufyLifeCloudError as err:
                raise HomeAssistantError(str(err)) from err
        self._set_power(
            True, round(brightness * 100 / 255) if brightness is not None else None
        )

    async def async_turn_off(self, **_kwargs: Any) -> None:
        """Ask the device to turn off."""
        self._set_power(False)

    async def async_set_light_settings(
        self,
        effect: str | None = None,
        colors: list[list[int]] | None = None,
        speed: int | None = None,
        direction: int | None = None,
    ) -> None:
        """Advanced control service: set effect, segmented colors, speed, or direction."""
        try:
            target_colors = None
            if colors is not None:
                target_colors = [tuple(c) for c in colors]
            await self._cloud.async_set_effect(
                self._device.serial,
                effect=effect,
                colors=target_colors,
                speed=speed,
                direction=direction,
            )
        except EufyLifeCloudError as err:
            raise HomeAssistantError(str(err)) from err

    async def async_set_light_show(
        self,
        params: str,
        use_ai_opcode: bool = False,
    ) -> None:
        """Advanced control service: set a custom JSON LightShow animation."""
        try:
            await self._cloud.async_set_effect(
                self._device.serial,
                params=params,
                use_ai_opcode=use_ai_opcode,
            )
        except EufyLifeCloudError as err:
            raise HomeAssistantError(str(err)) from err

    def _set_power(self, is_on: bool, brightness: int | None = None) -> None:
        try:
            self._cloud.set_power(self._device.serial, is_on, brightness)
        except EufyLifeCloudError as err:
            raise HomeAssistantError(str(err)) from err


class EufyLifeSegmentLight(LightEntity):
    """A single segment of a Eufy light string."""

    _attr_has_entity_name = True
    _attr_color_mode = ColorMode.RGBWW
    _attr_supported_color_modes = {ColorMode.RGBWW}
    # Segments don't have their own power/brightness in Eufy API, but we simulate it.
    _attr_assumed_state = True

    def __init__(self, cloud: EufyLifeLightCloud, device: EufyLifeLightDevice, index: int) -> None:
        self._cloud = cloud
        self._device = device
        self._index = index
        self._attr_unique_id = f"{device.serial}_segment_{index}"
        self._attr_name = f"Segment {index + 1}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.serial)},
        )

    @property
    def is_on(self) -> bool | None:
        """Return the device power state."""
        return self._device.is_on

    @property
    def brightness(self) -> int | None:
        """Return the device brightness."""
        value = self._device.brightness
        return round(value * 255 / 100) if value is not None else None

    @property
    def available(self) -> bool:
        """Return whether the cloud link and device are available."""
        return self._cloud.connected and self._device.online is not False

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        if self._device.colors and len(self._device.colors) > self._index:
            return self._device.colors[self._index][:3]
        return self._device.rgb_color

    @property
    def rgbww_color(self) -> tuple[int, int, int, int, int] | None:
        if self._device.colors and len(self._device.colors) > self._index:
            color = self._device.colors[self._index]
            if len(color) == 5:
                return color
            return (*color, 0, 0)
        return self._device.rgbww_color

    async def async_added_to_hass(self) -> None:
        """Subscribe to state changes."""
        await super().async_added_to_hass()
        self._cloud.add_listener(self._device.serial, self.async_write_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from state changes."""
        self._cloud.remove_listener(self._device.serial, self.async_write_ha_state)
        await super().async_will_remove_from_hass()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Set this segment's color, then ensure device is on."""
        if not self._device.lamp_count:
            raise HomeAssistantError("Lamp count is unknown")

        if ATTR_RGB_COLOR not in kwargs and ATTR_RGBWW_COLOR not in kwargs:
            # Just turning on or changing brightness; handled by main entity logic or global power.
            self._cloud.set_power(self._device.serial, True, kwargs.get(ATTR_BRIGHTNESS))
            return

        # Prepare full palette
        if self._device.colors:
            current_colors = [list(c) for c in self._device.colors]
        else:
            base = self._device.rgbww_color or self._device.rgb_color or (255, 255, 255, 0, 0)
            if len(base) == 3:
                base = (*base, 0, 0)
            current_colors = [list(base)] * self._device.lamp_count

        if ATTR_RGBWW_COLOR in kwargs:
            current_colors[self._index] = list(kwargs[ATTR_RGBWW_COLOR])
        elif ATTR_RGB_COLOR in kwargs:
            rgb = kwargs[ATTR_RGB_COLOR]
            current_colors[self._index] = [rgb[0], rgb[1], rgb[2], 0, 0]

        try:
            await self._cloud.async_set_effect(
                self._device.serial,
                colors=[tuple(c) for c in current_colors],
            )
        except EufyLifeCloudError as err:
            raise HomeAssistantError(str(err)) from err

    async def async_turn_off(self, **_kwargs: Any) -> None:
        """Turning off a segment is not supported individually; turn off the device."""
        self._cloud.set_power(self._device.serial, False)
