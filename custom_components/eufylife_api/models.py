"""Models for EufyLife API integration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .const import DEFAULT_COUNTRY

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .cloud import EufyLifeLightCloud


def entry_country(data: Mapping[str, Any]) -> str:
    """Return the country a config entry authenticates with.

    Entries created before lights were supported have no stored country and keep
    the "US" the integration always sent, so upgrading cannot change the region
    their account resolves to. Newer entries store the HA country at setup time.
    """
    country = data.get("country")
    if isinstance(country, str) and country:
        return country
    return DEFAULT_COUNTRY


@dataclass
class EufyLifeData:
    """Runtime data for EufyLife API integration."""

    email: str
    access_token: str
    user_id: str
    device_id: str | None
    customer_ids: list[str]
    expires_at: float
    user_center_id: str | None
    user_center_token: str | None
    openudid: str
    light_cloud: EufyLifeLightCloud | None = None


type EufyLifeConfigEntry = ConfigEntry[EufyLifeData]
