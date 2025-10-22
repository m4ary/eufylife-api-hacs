"""Authentication manager for EufyLife API."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

from .const import API_BASE_URL, CLIENT_ID, CLIENT_SECRET, USER_AGENT_VERSION

_LOGGER = logging.getLogger(__name__)

# Token refresh buffer: refresh 1 hour before expiration
TOKEN_REFRESH_BUFFER = 3600  # seconds


class EufyLifeAuthManager:
    """Manages authentication and token refresh for EufyLife API."""

    def __init__(self, session: aiohttp.ClientSession, email: str, password: str) -> None:
        """Initialize the auth manager.

        Args:
            session: aiohttp client session
            email: User email
            password: User password
        """
        self.session = session
        self.email = email
        self.password = password

    def should_refresh_token(self, expires_at: float) -> bool:
        """Check if token should be refreshed.

        Args:
            expires_at: Unix timestamp when token expires

        Returns:
            True if token should be refreshed (1 hour before expiration)
        """
        current_time = time.time()
        time_until_expiry = expires_at - current_time

        should_refresh = time_until_expiry <= TOKEN_REFRESH_BUFFER

        if should_refresh:
            _LOGGER.debug(
                "Token refresh needed: expires in %.1f minutes",
                time_until_expiry / 60
            )
        else:
            _LOGGER.debug(
                "Token still valid: expires in %.1f minutes",
                time_until_expiry / 60
            )

        return should_refresh

    async def authenticate(self) -> dict[str, Any] | None:
        """Authenticate with EufyLife API.

        Returns:
            Dictionary with auth data (access_token, user_id, expires_at, etc.) or None if failed
        """
        headers = {
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "User-Agent": f"EufyLife-iOS-{USER_AGENT_VERSION}-281",
            "Category": "Health",
            "Language": "en",
            "Timezone": "UTC",
            "Country": "US",
            "Content-Type": "application/json",
        }

        login_data = {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "email": self.email,
            "password": self.password,
        }

        try:
            async with self.session.post(
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
                else:
                    _LOGGER.error("Login request failed with status %d", response.status)
                    return None

        except asyncio.TimeoutError:
            _LOGGER.error("Timeout connecting to EufyLife API during authentication")
            return None
        except Exception as err:
            _LOGGER.error("Error authenticating with EufyLife API: %s", err)
            return None

    async def refresh_with_retry(self) -> dict[str, Any] | None:
        """Attempt to refresh token with retry logic.

        Tries up to 3 times with exponential backoff (1s, 5s, 15s).

        Returns:
            Dictionary with new auth data or None if all attempts failed
        """
        # Retry delays: immediate, 1s, 5s, 15s (exponential backoff)
        delays = [0, 1, 5, 15]

        for attempt, delay in enumerate(delays, 1):
            if delay > 0:
                _LOGGER.warning(
                    "Token refresh failed (attempt %d/%d), retrying in %d seconds...",
                    attempt - 1,
                    len(delays),
                    delay
                )
                await asyncio.sleep(delay)

            _LOGGER.debug("Attempting token refresh (attempt %d/%d)", attempt, len(delays))
            auth_data = await self.authenticate()

            if auth_data:
                _LOGGER.info(
                    "Token refreshed successfully on attempt %d (expires in %.1f days)",
                    attempt,
                    (auth_data["expires_at"] - time.time()) / 86400
                )
                return auth_data

        _LOGGER.error(
            "Token refresh failed after %d attempts - user re-authentication required",
            len(delays)
        )
        return None
