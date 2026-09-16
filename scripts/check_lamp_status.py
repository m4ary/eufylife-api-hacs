import asyncio
import os
import sys
import logging
from unittest.mock import MagicMock

for m in [
    "homeassistant",
    "homeassistant.config_entries",
    "homeassistant.const",
    "homeassistant.core",
    "homeassistant.exceptions",
    "homeassistant.helpers",
    "homeassistant.helpers.aiohttp_client",
    "homeassistant.helpers.config_validation",
    "voluptuous",
]:
    sys.modules[m] = MagicMock()

import aiohttp
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from custom_components.eufylife_api.cloud import async_login, EufyLifeLightCloud

logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')

async def main():
    email = os.environ.get("YOUR_EMAIL")
    password = os.environ.get("YOUR_PASSWORD")
    serial = os.environ.get("YOUR_SERIAL", "T8L4081024334CBC")
    country = "nl"

    if not email or not password:
        print("Please set YOUR_EMAIL and YOUR_PASSWORD")
        return

    async with aiohttp.ClientSession() as session:
        print("Logging in...")
        auth = await async_login(session, email, password, country)
        print("Starting cloud connection...")
        cloud = EufyLifeLightCloud(
            session=session,
            user_id=auth["user_id"],
            user_center_id=auth["user_center_id"],
            user_center_token=auth["user_center_token"],
            openudid="check-status-id",
            country=country,
            language="en",
            timezone="UTC"
        )
        await cloud.async_start()
        
        print("Waiting for device reports...")
        for _ in range(8):
            if cloud.connected and serial in cloud.devices:
                dev = cloud.devices[serial]
                if dev.is_on is not None:
                    break
            await asyncio.sleep(1)

        print("\n=== CURRENT LAMP STATUS ===")
        for s, dev in cloud.devices.items():
            print(f"Serial: {s} ({dev.name})")
            print(f"  Online: {dev.online}")
            print(f"  Power: {'ON' if dev.is_on else 'OFF'}")
            print(f"  Brightness: {dev.brightness}%")
            print(f"  Light ID: {dev.light_id}")
            print(f"  Effect: {dev.effect}")
            print(f"  Color: {dev.rgb_color}")
            print(f"  Segments: {dev.lamp_count}")

        # Request fresh settings and wait 3s to capture latest response
        print(f"\nRequesting fresh settings for {serial}...")
        cloud.request_settings(serial)
        await asyncio.sleep(3)

        dev = cloud.devices[serial]
        print(f"\nAfter refresh for {serial}:")
        print(f"  Power: {'ON' if dev.is_on else 'OFF'}")
        print(f"  Brightness: {dev.brightness}%")
        print(f"  Light ID: {dev.light_id}")
        print(f"  Effect: {dev.effect}")

        await cloud.async_close()

if __name__ == "__main__":
    asyncio.run(main())
