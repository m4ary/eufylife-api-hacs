"""The EufyLife API integration."""

from __future__ import annotations

import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .const import CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL, DOMAIN
from .models import EufyLifeData

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EufyLife API from a config entry."""
    _LOGGER.info("Setting up EufyLife API integration (entry_id: %s)", entry.entry_id)
    
    # Check if stored token is still valid
    stored_expires_at = entry.data.get("expires_at", 0)
    current_time = time.time()
    time_until_expiry = stored_expires_at - current_time
    
    _LOGGER.debug(
        "Token status check: expires_at=%s, current_time=%s, time_until_expiry=%.1f minutes",
        stored_expires_at,
        current_time,
        time_until_expiry / 60
    )
    
    if time_until_expiry <= 300:  # 5 minute buffer
        # Token expired, need to re-authenticate
        _LOGGER.warning(
            "Token expired or expiring soon! Time until expiry: %.1f minutes. "
            "Re-authentication will be required for API calls.",
            time_until_expiry / 60
        )
        # For now, we'll try with stored credentials
        # In production, you'd want to implement token refresh
    else:
        _LOGGER.info("Token is valid for %.1f more minutes", time_until_expiry / 60)
        
    # Create runtime data
    customer_ids = entry.data.get("customer_ids", [])
    _LOGGER.debug("Configuration data: email=%s, user_id=%s, device_id=%s, customer_ids=%s",
                 entry.data.get(CONF_EMAIL, "Not set"),
                 entry.data.get("user_id", "Not set")[:8] if entry.data.get("user_id") else "Not set",
                 entry.data.get("device_id", "Not set"),
                 [cid[:8] for cid in customer_ids] if customer_ids else "None")
    
    entry.runtime_data = EufyLifeData(
        email=entry.data[CONF_EMAIL],
        access_token=entry.data["access_token"],
        user_id=entry.data["user_id"],
        device_id=entry.data.get("device_id"),
        customer_ids=customer_ids,
        expires_at=entry.data.get("expires_at", 0),
    )

    # Log the configured update interval
    update_interval = entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
    _LOGGER.info("EufyLife integration setup with %d second update interval for %d customers", 
                update_interval, len(customer_ids))

    # Set up platforms
    _LOGGER.debug("Setting up platforms: %s", PLATFORMS)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Set up config entry update listener for dynamic interval changes
    entry.async_on_unload(entry.add_update_listener(async_update_listener))
    
    # Register developer services
    async def handle_refresh_data(call: ServiceCall) -> None:
        """Handle the refresh_data service call."""
        _LOGGER.info("Manual data refresh service called")
        
        # Get all coordinators for this domain
        coordinators = []
        for config_entry in hass.config_entries.async_entries(DOMAIN):
            if hasattr(config_entry, 'runtime_data'):
                # Find the coordinator in sensor platform
                sensor_platforms = hass.data.get("entity_platform", {}).get("sensor", [])
                for platform in sensor_platforms:
                    if platform.domain == DOMAIN and hasattr(platform, 'entities'):
                        for entity in platform.entities:
                            if hasattr(entity, 'coordinator'):
                                coordinators.append(entity.coordinator)
                                break
                        break
        
        # Refresh all coordinators
        for coordinator in coordinators:
            _LOGGER.info("Triggering manual refresh for coordinator")
            await coordinator.async_request_refresh()
    
    # Register the service
    if not hass.services.has_service(DOMAIN, "refresh_data"):
        hass.services.async_register(
            DOMAIN,
            "refresh_data",
            handle_refresh_data,
            schema=vol.Schema({
                vol.Optional("entity_id"): cv.entity_id,
            }),
        )
        _LOGGER.debug("Registered refresh_data service")
    
    _LOGGER.info("EufyLife API integration setup completed successfully")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading EufyLife API integration (entry_id: %s)", entry.entry_id)
    success = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if success:
        _LOGGER.info("EufyLife API integration unloaded successfully")
    else:
        _LOGGER.error("Failed to unload EufyLife API integration")
    return success


async def async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    _LOGGER.info("EufyLife integration options updated for entry %s, reloading...", entry.entry_id)
    
    # Log what changed
    new_interval = entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
    _LOGGER.debug("New update interval: %d seconds", new_interval)
    
    await hass.config_entries.async_reload(entry.entry_id)
    _LOGGER.info("EufyLife integration reload completed") 