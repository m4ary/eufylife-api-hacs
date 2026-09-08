"""Models for EufyLife API integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .cloud import EufyLifeLightCloud


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


EufyLifeConfigEntry = "ConfigEntry[EufyLifeData]"
