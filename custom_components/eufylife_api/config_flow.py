"""Config flow for EufyLife API integration."""

from __future__ import annotations

import logging
from typing import Any
import uuid

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import CountrySelector

from .cloud import EufyLifeAuthError, async_login
from .const import (
    CONF_COUNTRY,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    UPDATE_INTERVAL_OPTIONS,
)

_LOGGER = logging.getLogger(__name__)


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
            country = user_input[CONF_COUNTRY]
            update_interval_key = user_input[CONF_UPDATE_INTERVAL]
            update_interval = UPDATE_INTERVAL_OPTIONS[update_interval_key]

            # Test the connection
            try:
                auth_data = await self._test_connection(email, password, country)
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
                            CONF_COUNTRY: country,
                            CONF_UPDATE_INTERVAL: update_interval,
                            "user_id": auth_data["user_id"],
                            "access_token": auth_data["access_token"],
                            "user_center_id": auth_data.get("user_center_id"),
                            "user_center_token": auth_data.get("user_center_token"),
                            "openudid": str(uuid.uuid4()),
                            "expires_at": auth_data["expires_at"],
                            "device_id": auth_data.get("device_id"),
                            "customer_ids": auth_data.get("customer_ids", []),
                        },
                    )
                else:
                    errors["base"] = "invalid_auth"
            except EufyLifeAuthError:
                errors["base"] = "invalid_auth"
            except (aiohttp.ClientError, TimeoutError):
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_EMAIL,
                        default=(user_input or {}).get(CONF_EMAIL, ""),
                    ): str,
                    vol.Required(
                        CONF_PASSWORD,
                        default=(user_input or {}).get(CONF_PASSWORD, ""),
                    ): str,
                    vol.Required(
                        CONF_COUNTRY,
                        default=(user_input or {}).get(
                            CONF_COUNTRY, self.hass.config.country or "US"
                        ),
                    ): CountrySelector(),
                    vol.Optional(
                        CONF_UPDATE_INTERVAL,
                        default=(user_input or {}).get(
                            CONF_UPDATE_INTERVAL, "5 minutes"
                        ),
                    ): vol.In(UPDATE_INTERVAL_OPTIONS.keys()),
                }
            ),
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
                        vol.Required(
                            CONF_EMAIL, default=self._reauth_entry.data[CONF_EMAIL]
                        ): str,
                        vol.Required(CONF_PASSWORD): str,
                        vol.Required(
                            CONF_COUNTRY,
                            default=self._reauth_entry.data.get(
                                CONF_COUNTRY, self.hass.config.country or "US"
                            ),
                        ): CountrySelector(),
                    }
                ),
            )

        email = user_input[CONF_EMAIL]
        password = user_input[CONF_PASSWORD]
        country = user_input[CONF_COUNTRY]

        try:
            auth_data = await self._test_connection(email, password, country)
            if auth_data:
                # Update the existing entry with new credentials
                self.hass.config_entries.async_update_entry(
                    self._reauth_entry,
                    data={
                        **self._reauth_entry.data,
                        CONF_EMAIL: email,
                        CONF_PASSWORD: password,
                        CONF_COUNTRY: country,
                        "user_id": auth_data["user_id"],
                        "access_token": auth_data["access_token"],
                        "user_center_id": auth_data.get("user_center_id"),
                        "user_center_token": auth_data.get("user_center_token"),
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
                            vol.Required(CONF_COUNTRY, default=country): CountrySelector(),
                        }
                    ),
                    errors={"base": "invalid_auth"},
                )
        except EufyLifeAuthError:
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_EMAIL, default=email): str,
                        vol.Required(CONF_PASSWORD): str,
                        vol.Required(CONF_COUNTRY, default=country): CountrySelector(),
                    }
                ),
                errors={"base": "invalid_auth"},
            )
        except (aiohttp.ClientError, TimeoutError):
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_EMAIL, default=email): str,
                        vol.Required(CONF_PASSWORD): str,
                        vol.Required(CONF_COUNTRY, default=country): CountrySelector(),
                    }
                ),
                errors={"base": "cannot_connect"},
            )
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception during reauth")
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_EMAIL, default=email): str,
                        vol.Required(CONF_PASSWORD): str,
                        vol.Required(CONF_COUNTRY, default=country): CountrySelector(),
                    }
                ),
                errors={"base": "unknown"},
            )

    async def _test_connection(
        self, email: str, password: str, country: str
    ) -> dict[str, Any] | None:
        """Test if we can authenticate with the given credentials."""
        session = async_get_clientsession(self.hass)
        return await async_login(
            session,
            email,
            password,
            country,
        )


class EufyLifeAPIOptionsFlow(OptionsFlow):
    """Handle options flow for EufyLife API."""

    def __init__(self, config_entry: ConfigEntry) -> None:
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
        current_interval = self.config_entry.data.get(
            CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
        )
        current_key = next(
            (
                key
                for key, value in UPDATE_INTERVAL_OPTIONS.items()
                if value == current_interval
            ),
            "5 minutes",
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_UPDATE_INTERVAL, default=current_key): vol.In(
                        UPDATE_INTERVAL_OPTIONS.keys()
                    ),
                }
            ),
        )
