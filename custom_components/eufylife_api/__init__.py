"""The EufyLife API integration."""

from __future__ import annotations

import logging
import time

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .const import (
    API_BASE_URL,
    CLIENT_ID,
    CLIENT_SECRET,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)
from .models import EufyLifeData

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_refresh_token(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Silently re-authenticate using stored credentials and update the config entry.

    Returns True if the token was successfully refreshed, False otherwise.
    Falls back to triggering the reauth UI flow if called from setup context.
    """
    session = async_get_clientsession(hass)

    headers = {
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "User-Agent": "EufyLife-iOS-3.3.7",
        "Category": "Health",
        "Language": "en",
        "Timezone": "UTC",
        "Country": "US",
        "Content-Type": "application/json",
    }

    login_data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "email": entry.data[CONF_EMAIL],
        "password": entry.data[CONF_PASSWORD],
    }

    try:
        async with session.post(
            f"{API_BASE_URL}/v1/user/v2/email/login",
            headers=headers,
            json=login_data,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            if response.status == 200:
                data = await response.json()
                if data.get("res_code") == 1:
                    expires_in = data.get("expires_in", 2592000)  # 30 days default
                    new_token = data["access_token"]

                    hass.config_entries.async_update_entry(
                        entry,
                        data={
                            **entry.data,
                            "access_token": new_token,
                            "expires_at": time.time() + expires_in,
                        },
                    )

                    _LOGGER.info(
                        "Token silently refreshed, valid for %.1f days",
                        expires_in / 86400,
                    )
                    return True

                _LOGGER.warning(
                    "Silent token refresh rejected by API: res_code=%s, message=%s",
                    data.get("res_code"),
                    data.get("message", "Unknown error"),
                )
            else:
                _LOGGER.warning(
                    "Silent token refresh failed: HTTP %d", response.status
                )

    except aiohttp.ClientError as err:
        _LOGGER.error("Network error during silent token refresh: %s", err)
    except Exception as err:  # pylint: disable=broad-except
        _LOGGER.error("Unexpected error during silent token refresh: %s", err)

    return False


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EufyLife API from a config entry."""
    _LOGGER.info("Setting up EufyLife API integration (entry_id: %s)", entry.entry_id)

    # Check if stored token is still valid
    stored_expires_at = entry.data.get("expires_at", 0)
    time_until_expiry = stored_expires_at - time.time()

    _LOGGER.debug(
        "Token status: expires_at=%s, time_until_expiry=%.1f minutes",
        stored_expires_at,
        time_until_expiry / 60,
    )

    if time_until_expiry <= 300:  # 5 minute buffer
        _LOGGER.warning(
            "Token expired or expiring soon (%.1f min), attempting silent refresh...",
            time_until_expiry / 60,
        )
        refreshed = await async_refresh_token(hass, entry)
        if not refreshed:
            _LOGGER.error(
                "Silent token refresh failed — credentials may have changed. "
                "Triggering reauth UI."
            )
            entry.async_start_reauth(hass)
            raise ConfigEntryNotReady(
                "Token expired and silent refresh failed — please re-authenticate"
            )
    else:
        _LOGGER.info("Token is valid for %.1f more minutes", time_until_expiry / 60)

    # Create runtime data (use potentially-refreshed token from entry.data)
    customer_ids = entry.data.get("customer_ids", [])
    entry.runtime_data = EufyLifeData(
        email=entry.data[CONF_EMAIL],
        access_token=entry.data["access_token"],
        user_id=entry.data["user_id"],
        device_id=entry.data.get("device_id"),
        customer_ids=customer_ids,
        expires_at=entry.data.get("expires_at", 0),
    )

    update_interval = entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
    _LOGGER.info(
        "EufyLife integration setup with %ds update interval for %d customers",
        update_interval,
        len(customer_ids),
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_update_listener))

    # Register service for manual data refresh (e.g. from automations)
    async def handle_refresh_data(call: ServiceCall) -> None:
        """Handle the refresh_data service call."""
        _LOGGER.info("Manual data refresh service called")
        coordinators = hass.data.get(DOMAIN, {})
        for entry_id, coordinator in coordinators.items():
            _LOGGER.debug("Refreshing coordinator for entry %s", entry_id)
            await coordinator.async_request_refresh()

    if not hass.services.has_service(DOMAIN, "refresh_data"):
        hass.services.async_register(
            DOMAIN,
            "refresh_data",
            handle_refresh_data,
            schema=vol.Schema({vol.Optional("entity_id"): cv.entity_id}),
        )
        _LOGGER.debug("Registered refresh_data service")

    _LOGGER.info("EufyLife API integration setup completed successfully")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading EufyLife API integration (entry_id: %s)", entry.entry_id)
    success = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if success:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        _LOGGER.info("EufyLife API integration unloaded successfully")
    else:
        _LOGGER.error("Failed to unload EufyLife API integration")
    return success


async def async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    _LOGGER.info(
        "EufyLife integration options updated for entry %s, reloading...", entry.entry_id
    )
    await hass.config_entries.async_reload(entry.entry_id)
    _LOGGER.info("EufyLife integration reload completed")
