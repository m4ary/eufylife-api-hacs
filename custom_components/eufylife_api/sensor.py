"""Support for EufyLife API sensors."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Any

import aiohttp

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfMass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import API_BASE_URL, DOMAIN, SENSOR_TYPES, UPDATE_INTERVAL
from .models import EufyLifeConfigEntry

_LOGGER = logging.getLogger(__name__)


class EufyLifeDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the EufyLife API."""

    def __init__(self, hass: HomeAssistant, entry: EufyLifeConfigEntry) -> None:
        """Initialize."""
        self.entry = entry
        self.session = async_get_clientsession(hass)
        
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Update data via library."""
        try:
            return await self._fetch_data()
        except Exception as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err

    async def _fetch_data(self) -> dict[str, Any]:
        """Fetch data from EufyLife API."""
        data = self.entry.runtime_data
        
        # Check token expiry
        if time.time() >= data.expires_at - 300:  # 5 minute buffer
            _LOGGER.warning("Token expired, skipping update")
            return {}

        headers = {
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": "EufyLife-iOS-3.3.7",
            "Category": "Health",
            "Language": "en",
            "Timezone": "UTC",
            "Country": "US",
            "Token": data.access_token,
            "Uid": data.user_id,
        }

        try:
            # Fetch current weight targets
            async with self.session.get(
                f"{API_BASE_URL}/v1/customer/all_target",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status == 200:
                    target_data = await response.json()
                    if target_data.get("res_code") == 1:
                        return await self._process_target_data(target_data, headers)

            _LOGGER.error("Failed to fetch data from EufyLife API")
            return {}

        except asyncio.TimeoutError:
            _LOGGER.error("Timeout fetching data from EufyLife API")
            return {}
        except Exception as err:
            _LOGGER.error("Error fetching data from EufyLife API: %s", err)
            return {}

    async def _process_target_data(self, target_data: dict, headers: dict) -> dict[str, Any]:
        """Process target data and fetch additional customer details."""
        processed_data = {}
        
        target_list = target_data.get("target_list", [])
        
        for target in target_list:
            customer_id = target.get("customer_id")
            if not customer_id:
                continue
                
            # Get customer details for additional body composition data
            customer_details = await self._fetch_customer_details(customer_id, headers)
            
            # Process the data
            customer_data = {
                "weight": target.get("current_weight", 0) / 10.0,  # Convert from tenths
                "target_weight": target.get("target_weight", 0) / 10.0,
                "body_fat": target.get("current_bodyfat", 0),
                "muscle_mass": target.get("current_muscle_mass", 0),
                "last_update": datetime.fromtimestamp(target.get("update_time", 0)) if target.get("update_time") else None,
            }
            
            # Add customer details if available
            if customer_details:
                customer_data.update(customer_details)
            
            processed_data[customer_id] = customer_data
            
        return processed_data

    async def _fetch_customer_details(self, customer_id: str, headers: dict) -> dict[str, Any]:
        """Fetch detailed customer data."""
        try:
            async with self.session.get(
                f"{API_BASE_URL}/v1/customer/target/{customer_id}",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("res_code") == 1 and "target" in data:
                        target = data["target"]
                        return {
                            "detailed_weight": target.get("current_weight", 0) / 10.0,
                            "detailed_body_fat": target.get("current_bodyfat", 0),
                            "detailed_muscle_mass": target.get("current_muscle_mass", 0),
                            "target_body_fat": target.get("target_bodyfat", 0),
                        }
        except Exception as err:
            _LOGGER.debug("Error fetching customer details for %s: %s", customer_id, err)
            
        return {}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EufyLifeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EufyLife API sensor based on a config entry."""
    coordinator = EufyLifeDataUpdateCoordinator(hass, entry)
    
    # Fetch initial data so we have data when entities are added
    await coordinator.async_config_entry_first_refresh()
    
    entities = []
    
    # Create sensors for each customer
    for customer_id in entry.runtime_data.customer_ids:
        for sensor_type in SENSOR_TYPES:
            entities.append(
                EufyLifeSensorEntity(
                    coordinator=coordinator,
                    entry=entry,
                    customer_id=customer_id,
                    sensor_type=sensor_type,
                )
            )
    
    async_add_entities(entities)


class EufyLifeSensorEntity(CoordinatorEntity, SensorEntity):
    """Representation of a EufyLife sensor."""

    def __init__(
        self,
        coordinator: EufyLifeDataUpdateCoordinator,
        entry: EufyLifeConfigEntry,
        customer_id: str,
        sensor_type: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        
        self.entry = entry
        self.customer_id = customer_id
        self.sensor_type = sensor_type
        self._attr_unique_id = f"{entry.entry_id}_{customer_id}_{sensor_type}"
        
        sensor_config = SENSOR_TYPES[sensor_type]
        self._attr_name = f"{sensor_config['name']}"
        self._attr_icon = sensor_config.get("icon")
        
        # Set device class and units
        if sensor_config.get("device_class"):
            if sensor_config["device_class"] == "weight":
                self._attr_device_class = SensorDeviceClass.WEIGHT
                self._attr_native_unit_of_measurement = UnitOfMass.KILOGRAMS
            else:
                self._attr_native_unit_of_measurement = sensor_config.get("unit")
        else:
            self._attr_native_unit_of_measurement = sensor_config.get("unit")
            
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information about this EufyLife device."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.entry.entry_id}_{self.customer_id}")},
            name=f"EufyLife Customer {self.customer_id[:8]}",
            manufacturer="EufyLife",
            model="Smart Scale",
            sw_version="1.0.0",
        )

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        if not self.coordinator.data:
            return None
            
        customer_data = self.coordinator.data.get(self.customer_id)
        if not customer_data:
            return None
            
        value = customer_data.get(self.sensor_type)
        
        # Handle special cases
        if self.sensor_type in ["weight", "target_weight", "muscle_mass"] and value:
            return round(float(value), 1)
        elif self.sensor_type == "body_fat" and value:
            return round(float(value), 1)
            
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the state attributes."""
        if not self.coordinator.data:
            return None
            
        customer_data = self.coordinator.data.get(self.customer_id)
        if not customer_data:
            return None
            
        attrs = {}
        
        # Add last update time
        if customer_data.get("last_update"):
            attrs["last_update"] = customer_data["last_update"].isoformat()
            
        # Add customer ID for identification
        attrs["customer_id"] = self.customer_id[:8]
        
        return attrs 