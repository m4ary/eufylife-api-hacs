"""The EufyLife API integration."""

from __future__ import annotations

import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN
from .models import EufyLifeData

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EufyLife API from a config entry."""
    
    # Check if stored token is still valid
    stored_expires_at = entry.data.get("expires_at", 0)
    if time.time() >= stored_expires_at - 300:  # 5 minute buffer
        # Token expired, need to re-authenticate
        _LOGGER.warning("Token expired, re-authentication required")
        # For now, we'll try with stored credentials
        # In production, you'd want to implement token refresh
        
    # Create runtime data
    entry.runtime_data = EufyLifeData(
        email=entry.data[CONF_EMAIL],
        access_token=entry.data["access_token"],
        user_id=entry.data["user_id"],
        device_id=entry.data.get("device_id"),
        customer_ids=entry.data.get("customer_ids", []),
        expires_at=entry.data.get("expires_at", 0),
    )

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS) 