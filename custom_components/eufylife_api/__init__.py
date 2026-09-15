"""The EufyLife API integration."""

from __future__ import annotations

import logging
import time
import uuid

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .cloud import (
    EufyLifeAuthError,
    EufyLifeCloudError,
    EufyLifeLightCloud,
    async_login,
)
from .const import (
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)
from .models import EufyLifeData, entry_country

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.LIGHT]


async def async_refresh_token(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Silently re-authenticate using stored credentials and update the config entry.

    Returns True if the token was successfully refreshed, False otherwise.
    Falls back to triggering the reauth UI flow if called from setup context.
    """
    session = async_get_clientsession(hass)

    try:
        auth_data = await async_login(
            session,
            entry.data[CONF_EMAIL],
            entry.data[CONF_PASSWORD],
            entry_country(entry.data),
        )
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                "access_token": auth_data["access_token"],
                "user_id": auth_data["user_id"],
                "user_center_id": auth_data.get("user_center_id"),
                "user_center_token": auth_data.get("user_center_token"),
                "expires_at": auth_data["expires_at"],
            },
        )
        _LOGGER.info("Eufy Life token silently refreshed")
        return True
    except EufyLifeAuthError as err:
        _LOGGER.warning("Silent token refresh rejected by API: %s", err)
    except (aiohttp.ClientError, TimeoutError) as err:
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

    missing_light_tokens = not all(
        isinstance(entry.data.get(key), str) and entry.data[key]
        for key in ("user_center_id", "user_center_token")
    )
    token_expired = time_until_expiry <= 300  # Existing project buffer.
    if token_expired or missing_light_tokens:
        _LOGGER.warning(
            "Account tokens need refresh (expiry in %.1f min), re-authenticating...",
            time_until_expiry / 60,
        )
        refreshed = await async_refresh_token(hass, entry)
        if not refreshed and token_expired:
            _LOGGER.error(
                "Silent token refresh failed — credentials may have changed. "
                "Triggering reauth UI."
            )
            entry.async_start_reauth(hass)
            raise ConfigEntryNotReady(
                "Token expired and silent refresh failed — please re-authenticate"
            )
        if not refreshed:
            # The stored token still works; only the light tokens are missing, so
            # keep the scale running instead of sending the user through reauth.
            _LOGGER.warning(
                "Could not obtain light cloud tokens; scale sensors continue to "
                "work and lights stay unavailable until the next reload"
            )
    else:
        _LOGGER.info("Token is valid for %.1f more minutes", time_until_expiry / 60)

    openudid = entry.data.get("openudid")
    if not isinstance(openudid, str) or not openudid:
        openudid = str(uuid.uuid4())
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, "openudid": openudid}
        )

    # Create runtime data (use potentially-refreshed token from entry.data)
    customer_ids = entry.data.get("customer_ids", [])
    entry.runtime_data = EufyLifeData(
        email=entry.data[CONF_EMAIL],
        access_token=entry.data["access_token"],
        user_id=entry.data["user_id"],
        device_id=entry.data.get("device_id"),
        customer_ids=customer_ids,
        expires_at=entry.data.get("expires_at", 0),
        user_center_id=entry.data.get("user_center_id"),
        user_center_token=entry.data.get("user_center_token"),
        openudid=openudid,
    )

    center_id = entry.runtime_data.user_center_id
    center_token = entry.runtime_data.user_center_token
    if isinstance(center_id, str) and isinstance(center_token, str):
        light_cloud = EufyLifeLightCloud(
            async_get_clientsession(hass),
            entry.runtime_data.user_id,
            center_id,
            center_token,
            openudid,
            entry_country(entry.data),
            hass.config.language or "en",
            hass.config.time_zone,
        )
        try:
            await light_cloud.async_start()
        except (
            aiohttp.ClientError,
            TimeoutError,
            EufyLifeCloudError,
            OSError,
            ValueError,
        ) as err:
            await light_cloud.async_close()
            if not customer_ids:
                # Nothing else to serve, so let HA retry the whole entry later.
                raise ConfigEntryNotReady(
                    f"Eufy light cloud setup failed: {err}"
                ) from err
            _LOGGER.warning(
                "Eufy light cloud setup failed, continuing with scale sensors "
                "only; lights return after a reload: %s",
                err,
            )
        else:
            entry.runtime_data.light_cloud = light_cloud

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
        if entry.runtime_data.light_cloud is not None:
            await entry.runtime_data.light_cloud.async_close()
        _LOGGER.info("EufyLife API integration unloaded successfully")
    else:
        _LOGGER.error("Failed to unload EufyLife API integration")
    return success


async def async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    _LOGGER.info(
        "EufyLife integration options updated for entry %s, reloading...",
        entry.entry_id,
    )
    await hass.config_entries.async_reload(entry.entry_id)
    _LOGGER.info("EufyLife integration reload completed")
