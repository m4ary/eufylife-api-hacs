"""Config flow for EufyLife API integration."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    API_BASE_URL,
    CLIENT_ID,
    CLIENT_SECRET,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    UPDATE_INTERVAL_OPTIONS,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(
            CONF_UPDATE_INTERVAL, 
            default="5_minutes"
        ): vol.In(UPDATE_INTERVAL_OPTIONS.keys()),
    }
)


class EufyLifeAPIConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for EufyLife API."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize config flow."""
        self._reauth_entry = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Create the options flow."""
        return EufyLifeAPIOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL]
            password = user_input[CONF_PASSWORD]
            update_interval_key = user_input[CONF_UPDATE_INTERVAL]
            update_interval = UPDATE_INTERVAL_OPTIONS[update_interval_key]

            # Test the connection
            try:
                auth_data = await self._test_connection(email, password)
                if auth_data:
                    # Set unique ID based on user ID
                    await self.async_set_unique_id(auth_data["user_id"])
                    self._abort_if_unique_id_configured()

                    # Store the authentication data with update interval
                    return self.async_create_entry(
                        title=f"EufyLife ({email})",
                        data={
                            CONF_EMAIL: email,
                            CONF_PASSWORD: password,
                            CONF_UPDATE_INTERVAL: update_interval,
                            "user_id": auth_data["user_id"],
                            "access_token": auth_data["access_token"],
                            "expires_at": auth_data["expires_at"],
                            "device_id": auth_data.get("device_id"),
                            "customer_ids": auth_data.get("customer_ids", []),
                        },
                    )
                else:
                    errors["base"] = "invalid_auth"
            except aiohttp.ClientError:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle reauth upon an API authentication error."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Dialog that informs the user that reauth is required."""
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_EMAIL, default=self._reauth_entry.data[CONF_EMAIL]): str,
                        vol.Required(CONF_PASSWORD): str,
                    }
                ),
            )

        email = user_input[CONF_EMAIL]
        password = user_input[CONF_PASSWORD]

        try:
            auth_data = await self._test_connection(email, password)
            if auth_data:
                # Update the existing entry with new credentials
                self.hass.config_entries.async_update_entry(
                    self._reauth_entry,
                    data={
                        **self._reauth_entry.data,
                        CONF_EMAIL: email,
                        CONF_PASSWORD: password,
                        "access_token": auth_data["access_token"],
                        "expires_at": auth_data["expires_at"],
                    },
                )
                await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")
            else:
                return self.async_show_form(
                    step_id="reauth_confirm",
                    data_schema=vol.Schema(
                        {
                            vol.Required(CONF_EMAIL, default=email): str,
                            vol.Required(CONF_PASSWORD): str,
                        }
                    ),
                    errors={"base": "invalid_auth"},
                )
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception during reauth")
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_EMAIL, default=email): str,
                        vol.Required(CONF_PASSWORD): str,
                    }
                ),
                errors={"base": "unknown"},
            )

    async def _test_connection(self, email: str, password: str) -> dict[str, Any] | None:
        """Test if we can authenticate with the given credentials."""
        session = async_get_clientsession(self.hass)

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
            "email": email,
            "password": password,
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
                        access_token = data.get("access_token")
                        user_id = data.get("user_id")
                        expires_in = data.get("expires_in", 2592000)  # 30 days default
                        
                        if access_token and user_id:
                            # Extract device and customer info
                            devices = data.get("devices", [])
                            device_id = devices[0].get("id") if devices else None
                            
                            customers = data.get("customers", [])
                            customer_ids = [c.get("id") for c in customers if c.get("id")]
                            
                            return {
                                "access_token": access_token,
                                "user_id": user_id,
                                "expires_at": time.time() + expires_in,
                                "device_id": device_id,
                                "customer_ids": customer_ids,
                            }
                
                _LOGGER.error("Login failed: %s", data.get("message", "Unknown error"))
                return None
                
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout connecting to EufyLife API")
            return None
        except Exception as err:
            _LOGGER.error("Error connecting to EufyLife API: %s", err)
            return None


class EufyLifeAPIOptionsFlow(OptionsFlow):
    """Handle options flow for EufyLife API."""

    def __init__(self, config_entry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            update_interval = UPDATE_INTERVAL_OPTIONS[user_input[CONF_UPDATE_INTERVAL]]
            
            # Update the config entry data
            new_data = {**self.config_entry.data, CONF_UPDATE_INTERVAL: update_interval}
            self.hass.config_entries.async_update_entry(
                self.config_entry, data=new_data
            )
            
            return self.async_create_entry(title="", data={})

        # Get current interval
        current_interval = self.config_entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
        current_key = next(
            (key for key, value in UPDATE_INTERVAL_OPTIONS.items() if value == current_interval),
            "5_minutes"
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_UPDATE_INTERVAL, 
                        default=current_key
                    ): vol.In(UPDATE_INTERVAL_OPTIONS.keys()),
                }
            ),
        ) 