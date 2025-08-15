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

from .const import API_BASE_URL, CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL, DOMAIN, SENSOR_TYPES, USER_AGENT_VERSION
from .models import EufyLifeConfigEntry

_LOGGER = logging.getLogger(__name__)


class EufyLifeDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the EufyLife API."""

    def __init__(self, hass: HomeAssistant, entry: EufyLifeConfigEntry) -> None:
        """Initialize."""
        self.entry = entry
        self.session = async_get_clientsession(hass)
        self._last_update_time = None
        self._update_count = 0
        self._last_successful_update = None
        self._consecutive_failures = 0
        
        # Get update interval from config, fallback to default
        update_interval_seconds = entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
        
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(seconds=update_interval_seconds),
        )
        
        _LOGGER.info(
            "EufyLife data coordinator initialized with %d second update interval. "
            "Next update will be triggered automatically in %d seconds.",
            update_interval_seconds,
            update_interval_seconds
        )
        
        # Log customer IDs for debugging
        customer_ids = entry.runtime_data.customer_ids
        _LOGGER.debug("Customer IDs configured: %s", customer_ids)

    async def _async_update_data(self) -> dict[str, Any]:
        """Update data via library."""
        self._update_count += 1
        current_time = datetime.now()
        self._last_update_time = current_time
        
        _LOGGER.info(
            "Starting data update #%d at %s (interval: %ds, last successful: %s)",
            self._update_count,
            current_time.strftime('%Y-%m-%d %H:%M:%S'),
            self.update_interval.total_seconds(),
            self._last_successful_update.strftime('%Y-%m-%d %H:%M:%S') if self._last_successful_update else "Never"
        )
        
        try:
            data = await self._fetch_data()
            
            if data:
                self._last_successful_update = current_time
                self._consecutive_failures = 0
                _LOGGER.info(
                    "Data update #%d completed successfully. Retrieved data for %d customers. "
                    "Next update in %d seconds.",
                    self._update_count,
                    len(data),
                    self.update_interval.total_seconds()
                )
                _LOGGER.debug("Retrieved customer data keys: %s", list(data.keys()))
            else:
                self._consecutive_failures += 1
                _LOGGER.warning(
                    "Data update #%d returned empty data. Consecutive failures: %d",
                    self._update_count,
                    self._consecutive_failures
                )
            
            return data
            
        except Exception as err:
            self._consecutive_failures += 1
            _LOGGER.error(
                "Data update #%d failed with error: %s. Consecutive failures: %d",
                self._update_count,
                err,
                self._consecutive_failures
            )
            raise UpdateFailed(f"Error communicating with API: {err}") from err

    async def _fetch_data(self) -> dict[str, Any]:
        """Fetch data from EufyLife API."""
        data = self.entry.runtime_data
        
        # Try device data endpoint first (more recent data)
        device_data = await self._fetch_device_data()
        processed_device_data = {}
        if device_data:
            _LOGGER.info("Device data endpoint returned %d records, processing...", len(device_data))
            processed_device_data = await self._process_device_data(device_data)
        
        # Continue with existing target endpoint logic for fallback
        
        # Log token status
        current_time = time.time()
        token_expires_at = data.expires_at
        time_until_expiry = token_expires_at - current_time
        
        _LOGGER.debug(
            "Token status: expires_at=%s, current_time=%s, time_until_expiry=%.1f minutes",
            datetime.fromtimestamp(token_expires_at).strftime('%Y-%m-%d %H:%M:%S'),
            datetime.fromtimestamp(current_time).strftime('%Y-%m-%d %H:%M:%S'),
            time_until_expiry / 60
        )
        
        # Check token expiry with detailed logging
        if time_until_expiry <= 300:  # 5 minute buffer
            _LOGGER.error(
                "Token expired or expiring soon! Time until expiry: %.1f minutes. "
                "Re-authentication required. Skipping this update.",
                time_until_expiry / 60
            )
            return {}
        
        _LOGGER.debug("Token is valid, proceeding with API calls")

        headers = {
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": f"EufyLife-iOS-{USER_AGENT_VERSION}",
            "Category": "Health",
            "Language": "en",
            "Timezone": "UTC",
            "Country": "US",
            "Token": data.access_token,
            "Uid": data.user_id,
        }
        
        _LOGGER.debug("Making API request to: %s/v1/customer/all_target", API_BASE_URL)
        _LOGGER.debug("Request headers (token redacted): %s", {k: v if k != "Token" else "***REDACTED***" for k, v in headers.items()})

        try:
            # Fetch current weight targets
            start_time = time.time()
            async with self.session.get(
                f"{API_BASE_URL}/v1/customer/all_target",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                request_duration = time.time() - start_time
                
                _LOGGER.debug(
                    "API request completed in %.2f seconds. Status: %d, Headers: %s",
                    request_duration,
                    response.status,
                    dict(response.headers)
                )
                
                if response.status == 200:
                    target_data = await response.json()
                    _LOGGER.debug("API response received: %s", target_data)
                    
                    if target_data.get("res_code") == 1:
                        _LOGGER.info("API response successful, processing target data...")
                        target_processed_data = await self._process_target_data(target_data, headers)
                        
                        # Merge device data with target data, prioritizing device data
                        final_data = target_processed_data.copy()
                        for customer_id, device_info in processed_device_data.items():
                            if customer_id in final_data:
                                # Update existing customer data with device data
                                final_data[customer_id].update(device_info)
                                _LOGGER.debug("Updated customer %s with device data", customer_id[:8])
                            else:
                                # Add new customer from device data
                                final_data[customer_id] = device_info
                                _LOGGER.debug("Added new customer %s from device data", customer_id[:8])
                        
                        if processed_device_data:
                            _LOGGER.info("Merged device data for %d customers with target data", len(processed_device_data))
                        
                        return final_data
                    else:
                        _LOGGER.error(
                            "API returned error response. res_code: %s, message: %s",
                            target_data.get("res_code"),
                            target_data.get("res_msg", "Unknown error")
                        )
                        # Return device data only if target data failed
                        return processed_device_data
                else:
                    response_text = await response.text()
                    _LOGGER.error(
                        "API request failed with status %d. Response: %s",
                        response.status,
                        response_text[:500]  # Limit response text length
                    )
                    # Return device data if available, empty dict otherwise
                    return processed_device_data

        except asyncio.TimeoutError:
            _LOGGER.error(
                "Timeout fetching data from EufyLife API after 30 seconds. "
                "Check internet connection and API availability."
            )
            # Return device data if available, empty dict otherwise  
            return processed_device_data
        except aiohttp.ClientError as err:
            _LOGGER.error("HTTP client error fetching data from EufyLife API: %s", err)
            # Return device data if available, empty dict otherwise
            return processed_device_data
        except Exception as err:
            _LOGGER.error("Unexpected error fetching data from EufyLife API: %s", err, exc_info=True)
            # Return device data if available, empty dict otherwise
            return processed_device_data

    async def _fetch_device_data(self) -> dict[str, Any]:
        """Fetch recent device data from EufyLife API."""
        data = self.entry.runtime_data
        
        # Calculate timestamp for last 24 hours
        import time as time_module
        since_timestamp = int(time_module.time()) - 86400  # 24 hours ago
        
        headers = {
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": f"EufyLife-iOS-{USER_AGENT_VERSION}",
            "Token": data.access_token,
            "Uid": data.user_id,
        }
        
        try:
            start_time = time.time()
            async with self.session.get(
                f"{API_BASE_URL}/v1/device/data?after={since_timestamp}",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                request_duration = time.time() - start_time
                
                _LOGGER.debug(
                    "Device data request completed in %.2f seconds. Status: %d",
                    request_duration, response.status
                )
                
                if response.status == 200:
                    device_data = await response.json()
                    _LOGGER.debug("Device data response: %s", device_data)
                    
                    # Process device data if available
                    if device_data and isinstance(device_data, list) and len(device_data) > 0:
                        _LOGGER.info("Retrieved %d device data records", len(device_data))
                        return device_data
                    else:
                        _LOGGER.debug("No recent device data found")
                        return {}
                else:
                    _LOGGER.debug("Device data request failed with status %d", response.status)
                    return {}
                    
        except Exception as err:
            _LOGGER.debug("Error fetching device data: %s", err)
            return {}

    async def _process_device_data(self, device_data: list) -> dict[str, Any]:
        """Process device data into customer format."""
        processed_data = {}
        
        _LOGGER.debug("Processing %d device data records", len(device_data))
        
        for i, record in enumerate(device_data):
            try:
                # Extract customer ID - might be in different fields
                customer_id = record.get("customer_id") or record.get("customerId") or record.get("uid")
                if not customer_id:
                    _LOGGER.debug("Device record #%d missing customer_id, skipping", i)
                    continue
                
                # Extract measurement data
                # Device data might have current measurements in different format
                weight = None
                body_fat = None
                muscle_mass = None
                timestamp = None
                
                # Try different possible field names for weight (in grams, need to convert to kg)
                weight_raw = (record.get("weight") or 
                             record.get("current_weight") or 
                             record.get("bodyWeight") or 
                             record.get("body_weight"))
                
                if weight_raw:
                    # Device data weight might be in grams or already in kg with decimal
                    if isinstance(weight_raw, (int, float)):
                        if weight_raw > 1000:  # Assume grams if > 1000
                            weight = weight_raw / 1000.0
                        else:
                            weight = float(weight_raw)
                
                # Try different field names for body fat percentage
                body_fat = (record.get("body_fat") or 
                           record.get("bodyfat") or 
                           record.get("bodyFat") or 
                           record.get("current_bodyfat"))
                
                # Try different field names for muscle mass
                muscle_mass_raw = (record.get("muscle_mass") or 
                                  record.get("muscle") or 
                                  record.get("muscleMass") or 
                                  record.get("current_muscle_mass"))
                
                if muscle_mass_raw and isinstance(muscle_mass_raw, (int, float)):
                    muscle_mass = float(muscle_mass_raw)
                
                # Try different field names for timestamp
                timestamp_raw = (record.get("timestamp") or 
                                record.get("time") or 
                                record.get("created_at") or 
                                record.get("measureTime") or 
                                record.get("update_time"))
                
                if timestamp_raw:
                    try:
                        if isinstance(timestamp_raw, str):
                            # Try to parse ISO format timestamp
                            from datetime import datetime
                            timestamp = datetime.fromisoformat(timestamp_raw.replace('Z', '+00:00'))
                        elif isinstance(timestamp_raw, (int, float)):
                            timestamp = datetime.fromtimestamp(timestamp_raw)
                    except Exception as ts_err:
                        _LOGGER.debug("Could not parse timestamp %s: %s", timestamp_raw, ts_err)
                
                # Only process if we have some meaningful data
                if weight or body_fat or muscle_mass:
                    customer_data = {}
                    
                    if weight:
                        customer_data["weight"] = round(weight, 1)
                        customer_data["device_weight"] = True  # Mark as from device data
                    
                    if body_fat:
                        customer_data["body_fat"] = round(float(body_fat), 1)
                        customer_data["device_body_fat"] = True
                    
                    if muscle_mass:
                        customer_data["muscle_mass"] = round(muscle_mass, 1)
                        customer_data["device_muscle_mass"] = True
                    
                    if timestamp:
                        customer_data["last_update"] = timestamp
                        customer_data["device_timestamp"] = True
                    
                    # Merge with existing data for this customer or create new
                    if customer_id in processed_data:
                        processed_data[customer_id].update(customer_data)
                    else:
                        processed_data[customer_id] = customer_data
                    
                    _LOGGER.debug(
                        "Processed device record #%d for customer %s: weight=%s, body_fat=%s, muscle_mass=%s",
                        i, customer_id[:8] if customer_id else "unknown", weight, body_fat, muscle_mass
                    )
                else:
                    _LOGGER.debug("Device record #%d for customer %s contains no usable measurement data", 
                                 i, customer_id[:8] if customer_id else "unknown")
                    
            except Exception as err:
                _LOGGER.warning("Error processing device record #%d: %s", i, err)
                continue
        
        _LOGGER.info("Successfully processed device data for %d customers", len(processed_data))
        return processed_data

    async def _process_target_data(self, target_data: dict, headers: dict) -> dict[str, Any]:
        """Process target data and fetch additional customer details."""
        processed_data = {}
        
        target_list = target_data.get("target_list", [])
        _LOGGER.debug("Processing %d targets from API response", len(target_list))
        
        for i, target in enumerate(target_list):
            customer_id = target.get("customer_id")
            if not customer_id:
                _LOGGER.warning("Target #%d missing customer_id, skipping", i)
                continue
                
            _LOGGER.debug("Processing target #%d for customer %s", i, customer_id[:8])
            
            # Get customer details for additional body composition data
            customer_details = await self._fetch_customer_details(customer_id, headers)
            
            # Process the data with detailed logging
            raw_weight = target.get("current_weight", 0)
            raw_target_weight = target.get("target_weight", 0)
            raw_body_fat = target.get("current_bodyfat", 0)
            raw_muscle_mass = target.get("current_muscle_mass", 0)
            update_time = target.get("update_time", 0)
            
            _LOGGER.debug(
                "Raw data for customer %s: weight=%s, target_weight=%s, body_fat=%s, muscle_mass=%s, update_time=%s",
                customer_id[:8], raw_weight, raw_target_weight, raw_body_fat, raw_muscle_mass, update_time
            )
            
            customer_data = {
                "weight": raw_weight / 10.0 if raw_weight else 0,  # Convert from tenths
                "target_weight": raw_target_weight / 10.0 if raw_target_weight else 0,
                "body_fat": raw_body_fat,
                "muscle_mass": raw_muscle_mass,
                "last_update": datetime.fromtimestamp(update_time) if update_time else None,
            }
            
            # Calculate BMI if we have weight and height data
            if customer_data["weight"] > 0:
                # BMI calculation would need height data, for now we'll estimate
                customer_data["bmi"] = None  # Would need height from user profile
            
            # Add customer details if available
            if customer_details:
                _LOGGER.debug("Adding customer details: %s", customer_details)
                customer_data.update(customer_details)
            
            _LOGGER.debug(
                "Processed data for customer %s: %s",
                customer_id[:8],
                {k: v for k, v in customer_data.items() if k != "last_update"}
            )
            
            processed_data[customer_id] = customer_data
            
        _LOGGER.info("Successfully processed data for %d customers", len(processed_data))
        return processed_data

    async def _fetch_customer_details(self, customer_id: str, headers: dict) -> dict[str, Any]:
        """Fetch detailed customer data."""
        _LOGGER.debug("Fetching detailed data for customer %s", customer_id[:8])
        
        try:
            start_time = time.time()
            async with self.session.get(
                f"{API_BASE_URL}/v1/customer/target/{customer_id}",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                request_duration = time.time() - start_time
                
                _LOGGER.debug(
                    "Customer detail request for %s completed in %.2f seconds. Status: %d",
                    customer_id[:8], request_duration, response.status
                )
                
                if response.status == 200:
                    data = await response.json()
                    _LOGGER.debug("Customer detail response for %s: %s", customer_id[:8], data)
                    
                    if data.get("res_code") == 1 and "target" in data:
                        target = data["target"]
                        details = {
                            "detailed_weight": target.get("current_weight", 0) / 10.0,
                            "detailed_body_fat": target.get("current_bodyfat", 0),
                            "detailed_muscle_mass": target.get("current_muscle_mass", 0),
                            "target_body_fat": target.get("target_bodyfat", 0),
                        }
                        _LOGGER.debug("Extracted customer details for %s: %s", customer_id[:8], details)
                        return details
                    else:
                        _LOGGER.warning(
                            "Customer detail API returned error for %s. res_code: %s",
                            customer_id[:8], data.get("res_code")
                        )
                else:
                    _LOGGER.warning(
                        "Customer detail request failed for %s with status %d",
                        customer_id[:8], response.status
                    )
                    
        except Exception as err:
            _LOGGER.warning("Error fetching customer details for %s: %s", customer_id[:8], err)
            
        return {}

    def update_interval_from_config(self) -> None:
        """Update the coordinator's update interval from config entry."""
        new_interval_seconds = self.entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
        new_interval = timedelta(seconds=new_interval_seconds)
        
        if new_interval != self.update_interval:
            _LOGGER.info(
                "Updating coordinator interval from %s to %s seconds. "
                "Next update will be rescheduled accordingly.",
                self.update_interval.total_seconds(),
                new_interval_seconds
            )
            self.update_interval = new_interval
            
    async def async_request_refresh(self) -> None:
        """Request a manual refresh of data."""
        _LOGGER.info("Manual data refresh requested")
        await super().async_request_refresh()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EufyLifeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EufyLife API sensor based on a config entry."""
    _LOGGER.info("Setting up EufyLife API sensors for entry %s", entry.entry_id)
    
    coordinator = EufyLifeDataUpdateCoordinator(hass, entry)
    
    # Fetch initial data so we have data when entities are added
    _LOGGER.info("Performing initial data refresh...")
    await coordinator.async_config_entry_first_refresh()
    
    entities = []
    
    # Create sensors for each customer
    customer_ids = entry.runtime_data.customer_ids
    _LOGGER.info("Creating sensors for %d customers", len(customer_ids))
    
    for customer_id in customer_ids:
        _LOGGER.debug("Creating sensors for customer %s", customer_id[:8])
        for sensor_type in SENSOR_TYPES:
            entity = EufyLifeSensorEntity(
                coordinator=coordinator,
                entry=entry,
                customer_id=customer_id,
                sensor_type=sensor_type,
            )
            entities.append(entity)
            _LOGGER.debug("Created %s sensor for customer %s", sensor_type, customer_id[:8])
    
    _LOGGER.info("Adding %d sensor entities to Home Assistant", len(entities))
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
            _LOGGER.debug("No coordinator data available for %s sensor (customer %s)", 
                         self.sensor_type, self.customer_id[:8])
            return None
            
        customer_data = self.coordinator.data.get(self.customer_id)
        if not customer_data:
            _LOGGER.debug("No customer data available for %s sensor (customer %s)", 
                         self.sensor_type, self.customer_id[:8])
            return None
            
        value = customer_data.get(self.sensor_type)
        
        _LOGGER.debug("Raw value for %s sensor (customer %s): %s", 
                     self.sensor_type, self.customer_id[:8], value)
        
        # Handle special cases
        if self.sensor_type in ["weight", "target_weight", "muscle_mass"] and value:
            processed_value = round(float(value), 1)
            _LOGGER.debug("Processed %s value for customer %s: %s -> %s", 
                         self.sensor_type, self.customer_id[:8], value, processed_value)
            return processed_value
        elif self.sensor_type == "body_fat" and value:
            processed_value = round(float(value), 1)
            _LOGGER.debug("Processed %s value for customer %s: %s -> %s", 
                         self.sensor_type, self.customer_id[:8], value, processed_value)
            return processed_value
            
        _LOGGER.debug("Returning unprocessed %s value for customer %s: %s", 
                     self.sensor_type, self.customer_id[:8], value)
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
        
        # Add update interval info
        interval_seconds = self.entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
        attrs["update_interval"] = f"{interval_seconds} seconds"
        
        # Add coordinator status for debugging
        if hasattr(self.coordinator, '_update_count'):
            attrs["update_count"] = self.coordinator._update_count
            attrs["consecutive_failures"] = self.coordinator._consecutive_failures
            if self.coordinator._last_successful_update:
                attrs["last_successful_update"] = self.coordinator._last_successful_update.isoformat()
        
        # Add data source information
        data_sources = []
        if customer_data.get("device_weight") or customer_data.get("device_body_fat") or customer_data.get("device_muscle_mass"):
            data_sources.append("device_data")
        if any(key not in ["device_weight", "device_body_fat", "device_muscle_mass", "device_timestamp"] 
               for key in customer_data.keys() if key.startswith(("weight", "body_fat", "muscle_mass", "target_"))):
            data_sources.append("target_data")
        
        if data_sources:
            attrs["data_source"] = ", ".join(data_sources)
            
        # Add specific device data indicators for this sensor type
        device_data_key = f"device_{self.sensor_type}"
        if customer_data.get(device_data_key):
            attrs["from_device_data"] = True
        
        _LOGGER.debug("Attributes for %s sensor (customer %s): %s", 
                     self.sensor_type, self.customer_id[:8], attrs)
        
        return attrs 