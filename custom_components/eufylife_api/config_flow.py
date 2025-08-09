"""Config flow for EufyLife API integration."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import API_BASE_URL, CLIENT_ID, CLIENT_SECRET, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class EufyLifeAPIConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for EufyLife API."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL]
            password = user_input[CONF_PASSWORD]

            # Test the connection
            try:
                auth_data = await self._test_connection(email, password)
                if auth_data:
                    # Set unique ID based on user ID
                    await self.async_set_unique_id(auth_data["user_id"])
                    self._abort_if_unique_id_configured()

                    # Store the authentication data
                    return self.async_create_entry(
                        title=f"EufyLife ({email})",
                        data={
                            CONF_EMAIL: email,
                            CONF_PASSWORD: password,
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