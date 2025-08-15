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

from .const import API_BASE_URL, CONF_DATA_LOOKBACK_DAYS, CONF_UPDATE_INTERVAL, DEFAULT_DATA_LOOKBACK_DAYS, DEFAULT_UPDATE_INTERVAL, DOMAIN, SENSOR_TYPES, USER_AGENT_VERSION
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
        self._last_device_timestamp = None  # Track last measurement timestamp
        
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
        """Fetch data from EufyLife API using device data endpoint only."""
        data = self.entry.runtime_data
        
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
        
        _LOGGER.debug("Token is valid, proceeding with device data API call")
        
        # Fetch device data only (most recent and reliable data)
        device_data = await self._fetch_device_data()
        if device_data:
            _LOGGER.info("Device data endpoint returned %d records, processing...", len(device_data))
            processed_device_data = await self._process_device_data(device_data)
            
            # Log what data we received
            if processed_device_data:
                for customer_id, customer_data in processed_device_data.items():
                    last_update = customer_data.get("last_update")
                    if last_update:
                        _LOGGER.info("Customer %s: latest measurement at %s", 
                                   customer_id[:8], last_update.strftime('%Y-%m-%d %H:%M:%S'))
            
            return processed_device_data
        else:
            _LOGGER.warning("No device data available from API")
            return {}

    async def _fetch_device_data(self) -> dict[str, Any]:
        """Fetch recent device data from EufyLife API."""
        data = self.entry.runtime_data
        
        import time as time_module
        
        # Use last device timestamp if available, otherwise use lookback period
        if self._last_device_timestamp:
            # Get data since last measurement (add 1 second to avoid duplicates)
            since_timestamp = int(self._last_device_timestamp) + 1
            _LOGGER.info(
                "Fetching device data since last measurement: timestamp %d (%s)",
                since_timestamp,
                datetime.fromtimestamp(since_timestamp).strftime('%Y-%m-%d %H:%M:%S')
            )
        else:
            # First run - use configurable lookback period
            lookback_days = self.entry.data.get(CONF_DATA_LOOKBACK_DAYS, DEFAULT_DATA_LOOKBACK_DAYS)
            since_timestamp = int(time_module.time()) - (lookback_days * 24 * 3600)
            _LOGGER.info(
                "First run - fetching device data since timestamp %d (%d days lookback, %s)",
                since_timestamp,
                lookback_days,
                datetime.fromtimestamp(since_timestamp).strftime('%Y-%m-%d %H:%M:%S')
            )
        
        headers = {
            "Host": "api.eufylife.com",
            "Accept": "*/*",
            "Uid": data.user_id,
            "Accept-Encoding": "gzip, deflate, br",
            "User-Agent": f"Eufylife-iOS-{USER_AGENT_VERSION}-281",
            "Accept-Language": "en-US,en;q=0.9",
            "Token": data.access_token,
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
                    
                    # Extract the actual data array from the response
                    actual_data = []
                    if isinstance(device_data, dict):
                        # API returns structured response like {'res_code': 1, 'data': [...]}
                        actual_data = device_data.get('data', [])
                        res_code = device_data.get('res_code')
                        message = device_data.get('message', '')
                        
                        _LOGGER.info(
                            "Device data API response: res_code=%s, message='%s', records=%d",
                            res_code, message, len(actual_data) if actual_data else 0
                        )
                        
                        if res_code != 1:
                            _LOGGER.warning("Device data API returned error: res_code=%s, message='%s'", res_code, message)
                            return {}
                    elif isinstance(device_data, list):
                        # Direct list response
                        actual_data = device_data
                        _LOGGER.info("Device data API returned direct list with %d records", len(actual_data))
                    
                    # Process device data if available
                    if actual_data and len(actual_data) > 0:
                        _LOGGER.info("Retrieved %d device data records for processing", len(actual_data))
                        
                        # Log sample of customer IDs found in data for debugging
                        customer_ids_found = set()
                        for record in actual_data[:5]:  # Check first 5 records
                            cust_id = (record.get("customer_id") or 
                                      record.get("customerId") or 
                                      record.get("uid") or 
                                      record.get("user_id"))
                            if cust_id:
                                customer_ids_found.add(cust_id[:8] + "...")
                        
                        if customer_ids_found:
                            _LOGGER.info("Sample customer IDs found in device data: %s", list(customer_ids_found))
                        
                        return actual_data
                    else:
                        if self._last_device_timestamp:
                            _LOGGER.warning(
                                "No new device data found since last measurement (%s). "
                                "Take a new measurement on your scale to generate fresh data.",
                                datetime.fromtimestamp(since_timestamp - 1).strftime('%Y-%m-%d %H:%M:%S')
                            )
                        else:
                            lookback_days = self.entry.data.get(CONF_DATA_LOOKBACK_DAYS, DEFAULT_DATA_LOOKBACK_DAYS)
                            _LOGGER.warning(
                                "No device data found in last %d days (since %s). "
                                "Try taking a measurement on your scale to generate new data.",
                                lookback_days,
                                datetime.fromtimestamp(since_timestamp).strftime('%Y-%m-%d %H:%M:%S')
                            )
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
        latest_timestamp = self._last_device_timestamp
        total_measurements = len(device_data)
        
        _LOGGER.info("Processing %d device data records (measurements)", total_measurements)
        if self._last_device_timestamp:
            _LOGGER.debug("Current last device timestamp: %s", 
                         datetime.fromtimestamp(self._last_device_timestamp).strftime('%Y-%m-%d %H:%M:%S'))
        
        for i, record in enumerate(device_data):
            try:
                # Extract customer ID
                customer_id = record.get("customer_id")
                if not customer_id:
                    _LOGGER.debug("Device record #%d missing customer_id, skipping", i)
                    continue
                
                # Extract scale_data (the main measurement data)
                scale_data = record.get("scale_data", {})
                if not scale_data:
                    _LOGGER.debug("Device record #%d for customer %s missing scale_data, skipping", 
                                 i, customer_id[:8])
                    continue
                
                # Extract timestamp from update_time
                timestamp = None
                update_time = record.get("update_time") or record.get("create_time")
                if update_time:
                    try:
                        timestamp = datetime.fromtimestamp(update_time)
                        # Track the latest timestamp for next API call
                        if latest_timestamp is None or update_time > latest_timestamp:
                            latest_timestamp = update_time
                    except Exception as ts_err:
                        _LOGGER.debug("Could not parse timestamp %s: %s", update_time, ts_err)
                
                # Extract measurements from scale_data
                customer_data = {}
                
                # Weight (convert from decigrams to kg)
                weight_decigrams = scale_data.get("weight")
                if weight_decigrams and isinstance(weight_decigrams, (int, float)):
                    customer_data["weight"] = round(weight_decigrams / 10.0, 2)
                    customer_data["device_weight"] = True
                
                # Body Fat percentage
                body_fat = scale_data.get("body_fat")
                if body_fat and isinstance(body_fat, (int, float)):
                    customer_data["body_fat"] = round(float(body_fat), 2)
                    customer_data["device_body_fat"] = True
                
                # Muscle Mass (already in kg)
                muscle_mass = scale_data.get("muscle_mass")
                if muscle_mass and isinstance(muscle_mass, (int, float)):
                    customer_data["muscle_mass"] = round(float(muscle_mass), 2)
                    customer_data["device_muscle_mass"] = True
                
                # BMI (already calculated by the scale)
                bmi = scale_data.get("bmi")
                if bmi and isinstance(bmi, (int, float)):
                    customer_data["bmi"] = round(float(bmi), 2)
                    customer_data["device_bmi"] = True
                
                # Additional body composition data
                water_percentage = scale_data.get("water")
                if water_percentage and isinstance(water_percentage, (int, float)):
                    customer_data["water_percentage"] = round(float(water_percentage), 2)
                    customer_data["device_water_percentage"] = True
                
                bone_mass = scale_data.get("bone_mass")
                if bone_mass and isinstance(bone_mass, (int, float)):
                    customer_data["bone_mass"] = round(float(bone_mass), 2)
                    customer_data["device_bone_mass"] = True
                
                bmr = scale_data.get("bmr")
                if bmr and isinstance(bmr, (int, float)):
                    customer_data["bmr"] = int(bmr)
                    customer_data["device_bmr"] = True
                
                body_age = scale_data.get("body_age")
                if body_age and isinstance(body_age, (int, float)):
                    customer_data["body_age"] = int(body_age)
                    customer_data["device_body_age"] = True
                
                visceral_fat = scale_data.get("visceral_fat")
                if visceral_fat and isinstance(visceral_fat, (int, float)):
                    customer_data["visceral_fat"] = round(float(visceral_fat), 2)
                    customer_data["device_visceral_fat"] = True
                
                protein_ratio = scale_data.get("protein_ratio")
                if protein_ratio and isinstance(protein_ratio, (int, float)):
                    customer_data["protein_ratio"] = round(float(protein_ratio), 2)
                    customer_data["device_protein_ratio"] = True
                
                # Add timestamp
                if timestamp:
                    customer_data["last_update"] = timestamp
                    customer_data["device_timestamp"] = True
                
                # Add device info
                device_id = record.get("device_id")
                product_code = record.get("product_code")
                if device_id:
                    customer_data["device_id"] = device_id
                if product_code:
                    customer_data["product_code"] = product_code
                
                # Only process if we have some meaningful measurement data
                if any(key in customer_data for key in ["weight", "body_fat", "muscle_mass", "bmi"]):
                    # If we already have data for this customer, keep the most recent one
                    if customer_id in processed_data:
                        existing_timestamp = processed_data[customer_id].get("last_update")
                        if timestamp and existing_timestamp and timestamp > existing_timestamp:
                            processed_data[customer_id] = customer_data
                            _LOGGER.debug("Updated customer %s with newer measurement (timestamp: %s)", 
                                         customer_id[:8], timestamp.strftime('%Y-%m-%d %H:%M:%S'))
                        else:
                            _LOGGER.debug("Keeping existing measurement for customer %s (older or same timestamp)", 
                                         customer_id[:8])
                    else:
                        processed_data[customer_id] = customer_data
                    
                    _LOGGER.debug(
                        "Processed device record #%d for customer %s: weight=%s kg, body_fat=%s%%, muscle_mass=%s kg, bmi=%s, timestamp=%s",
                        i, customer_id[:8], 
                        customer_data.get("weight"), 
                        customer_data.get("body_fat"),
                        customer_data.get("muscle_mass"),
                        customer_data.get("bmi"),
                        timestamp.strftime('%Y-%m-%d %H:%M:%S') if timestamp else "None"
                    )
                else:
                    _LOGGER.debug("Device record #%d for customer %s contains no usable measurement data", 
                                 i, customer_id[:8])
                    
            except Exception as err:
                _LOGGER.warning("Error processing device record #%d: %s", i, err)
                continue
        
        # Update the last device timestamp for next API call
        if latest_timestamp is not None:
            self._last_device_timestamp = latest_timestamp
            _LOGGER.info("Updated last device timestamp to %s for next API call", 
                        datetime.fromtimestamp(latest_timestamp).strftime('%Y-%m-%d %H:%M:%S'))
        
        _LOGGER.info("Successfully processed %d measurements into data for %d customers", 
                    total_measurements, len(processed_data))
        
        if total_measurements > len(processed_data):
            _LOGGER.debug("Some measurements were for the same customers - kept most recent per customer")
        
        return processed_data



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
    
    def reset_device_timestamp(self) -> None:
        """Reset the device timestamp to force full data reload on next update."""
        self._last_device_timestamp = None
        _LOGGER.info("Device timestamp reset - next update will use full lookback period")


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
        
        # Handle special cases for different measurement types
        if self.sensor_type in ["weight", "target_weight", "muscle_mass", "bone_mass"] and value:
            processed_value = round(float(value), 2)
            _LOGGER.debug("Processed %s value for customer %s: %s -> %s", 
                         self.sensor_type, self.customer_id[:8], value, processed_value)
            return processed_value
        elif self.sensor_type in ["body_fat", "water_percentage", "visceral_fat", "protein_ratio", "bmi"] and value:
            processed_value = round(float(value), 2)
            _LOGGER.debug("Processed %s value for customer %s: %s -> %s", 
                         self.sensor_type, self.customer_id[:8], value, processed_value)
            return processed_value
        elif self.sensor_type in ["bmr", "body_age"] and value:
            processed_value = int(value)
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
        
        # Add data source information (device data only)
        attrs["data_source"] = "device_data"
            
        # Add specific device data indicators for this sensor type
        device_data_key = f"device_{self.sensor_type}"
        if customer_data.get(device_data_key):
            attrs["from_device_data"] = True
            
        # Add device information if available
        if customer_data.get("device_id"):
            attrs["device_id"] = customer_data["device_id"]
        if customer_data.get("product_code"):
            attrs["product_code"] = customer_data["product_code"]
        
        _LOGGER.debug("Attributes for %s sensor (customer %s): %s", 
                     self.sensor_type, self.customer_id[:8], attrs)
        
        return attrs 