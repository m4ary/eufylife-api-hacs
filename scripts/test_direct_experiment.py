import asyncio
import os
import sys
import logging
import json
import time
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
from custom_components.eufylife_api.cloud import (
    async_login,
    EufyLifeLightCloud,
    _tlv,
    _tlv_long,
    _command_payload,
    _parse_tlvs,
    _effect_payload,
)

logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')

GUY_FAWKES_JSON = '{"light_effect_speed":54,"layer_execution_mode":3,"layer":[{"layer_priority":1,"layer_speed":100,"layer_range":[0,100],"interval_type":1,"interval_value":1,"layer_execution_parameter":130,"light_effect_post_cycle_status":1,"current_layer_type":0,"colors":"ff4d6a|ffa36a|ff6a8a|a36aff|6affff","color_fill_mode":0,"color_pick_mode":0,"flow_direction":0,"direction_change_mode":0,"insert_block_mode":0,"insert_block_range":3,"insert_black_block_mode":0,"insert_black_block_range":9,"brightness_variation_type":4,"brightness_range":[16,100],"light_effect_cycle_method":2,"execution_parameter":12},{"layer_priority":0,"layer_speed":20,"layer_range":[75,100],"interval_type":1,"interval_value":1,"layer_execution_parameter":1,"light_effect_post_cycle_status":3,"current_layer_type":1,"colors":"6affff|a36aff|ff6a8a|a8ff59|ff4d6a","brightness_value":97,"display_mode":1,"color_quantity_range":2,"transition_mode":2,"color_switch_mode":0,"color_pick_sequence":1,"light_effect_cycle_method":0,"execution_parameter":2}]}'
GUY_FAWKES_RGB = "f49e9e|f49ea9|f49eb5|f49ec0|f49ecb|f49ed7|f49ee2|f49eed|ef9ef4|d99ef4|e49ef4|cd9ef4"

async def run_experiment(scenario: str):
    email = os.environ.get("YOUR_EMAIL")
    password = os.environ.get("YOUR_PASSWORD")
    serial = os.environ.get("YOUR_SERIAL", "T8L4081024334CBC")
    country = "nl"

    async with aiohttp.ClientSession() as session:
        auth = await async_login(session, email, password, country)
        cloud = EufyLifeLightCloud(
            session=session,
            user_id=auth["user_id"],
            user_center_id=auth["user_center_id"],
            user_center_token=auth["user_center_token"],
            openudid="experiment-id",
            country=country,
            language="en",
            timezone="UTC"
        )
        await cloud.async_start()

        for _ in range(8):
            if cloud.connected and serial in cloud.devices and cloud.devices[serial].is_on is not None:
                break
            await asyncio.sleep(1)

        dev = cloud.devices[serial]
        print(f"\n--- Initial State for {dev.name} [{serial}] ---")
        print(f"Power: {dev.is_on}, Brightness: {dev.brightness}%, Light ID: {dev.light_id}")

        cloud_id = 10854

        if scenario == "1":
            # Candidate 1: Opcode 0210 (LightShow) Standard Payload with A3 (2b), A4 (JSON long TLV), A8-B0 + Envelope
            print("\n>>> Testing Scenario 1: Opcode 0210 + A3(2b) + A4(JSON) + A8-B0 with Envelope (v0)")
            value = (
                _tlv_long(0xA3, cloud_id.to_bytes(2, "little"))
                + _tlv_long(0xA4, GUY_FAWKES_JSON.encode())
                + _tlv(0xA8, b"\x64")
                + _tlv(0xA9, bytes(5))
                + _tlv(0xAA, b"\x00")
                + _tlv(0xAE, b"\x00")
                + _tlv(0xB0, b"\x00")
            )
            payload = _command_payload(cloud._user_id, value)
            cloud._publish(serial, (0x02, 0x10), payload, version=0)

        elif scenario == "2":
            # Candidate 2: Opcode 0211 (AI) Standard Payload with A3 (2b), A4 (JSON long TLV), A8-B0 + Envelope
            print("\n>>> Testing Scenario 2: Opcode 0211 + A3(2b) + A4(JSON) + A8-B0 with Envelope (v0)")
            value = (
                _tlv_long(0xA3, cloud_id.to_bytes(2, "little"))
                + _tlv_long(0xA4, GUY_FAWKES_JSON.encode())
                + _tlv(0xA8, b"\x64")
                + _tlv(0xA9, bytes(5))
                + _tlv(0xAA, b"\x00")
                + _tlv(0xAE, b"\x00")
                + _tlv(0xB0, b"\x00")
            )
            payload = _command_payload(cloud._user_id, value)
            cloud._publish(serial, (0x02, 0x11), payload, version=0)

        elif scenario == "3":
            # Candidate 3: Opcode 0210 with A3 (4b cloud_id) + A4 (JSON) + A8-B0 with Envelope
            print("\n>>> Testing Scenario 3: Opcode 0210 + A3(4b) + A4(JSON) + A8-B0 with Envelope (v0)")
            value = (
                _tlv_long(0xA3, cloud_id.to_bytes(4, "little"))
                + _tlv_long(0xA4, GUY_FAWKES_JSON.encode())
                + _tlv(0xA8, b"\x64")
                + _tlv(0xA9, bytes(5))
                + _tlv(0xAA, b"\x00")
                + _tlv(0xAE, b"\x00")
                + _tlv(0xB0, b"\x00")
            )
            payload = _command_payload(cloud._user_id, value)
            cloud._publish(serial, (0x02, 0x10), payload, version=0)

        elif scenario == "4":
            # Candidate 4: Opcode 0206 with _effect_payload (classic)
            print("\n>>> Testing Scenario 4: Opcode 0206 Classic Effect Payload (dynamic=20006, 12 colors, cloud_id=10854)")
            colors = [tuple(bytes.fromhex(c)) for c in GUY_FAWKES_RGB.split("|")]
            value = _effect_payload(20006, colors, 0, 1, cloud_id, update_method=0)
            payload = _command_payload(cloud._user_id, value)
            cloud._publish(serial, (0x02, 0x06), payload, version=0)

        elif scenario == "5":
            # Candidate 5: Opcode 0206 with dynamic=44 (effect) but cloud_id=10854
            print("\n>>> Testing Scenario 5: Opcode 0206 Classic Effect Payload (dynamic=44, colors, cloud_id=10854)")
            colors = [tuple(bytes.fromhex(c)) for c in GUY_FAWKES_RGB.split("|")][:3]
            value = _effect_payload(44, colors, 0, 1, cloud_id, update_method=0)
            payload = _command_payload(cloud._user_id, value)
            cloud._publish(serial, (0x02, 0x06), payload, version=0)

        elif scenario == "6":
            # Candidate 6: Opcode 0206 with light_id=10854, 12 colors, cloud_id=10854
            print("\n>>> Testing Scenario 6: Opcode 0206 with light_id=10854 and cloud_id=10854")
            colors = [tuple(bytes.fromhex(c)) for c in GUY_FAWKES_RGB.split("|")]
            value = _effect_payload(10854, colors, 0, 1, cloud_id, update_method=0)
            payload = _command_payload(cloud._user_id, value)
            cloud._publish(serial, (0x02, 0x06), payload, version=0)

        elif scenario == "7":
            # Candidate 7: Celebrating (dynamic=31, 10 colors, speed=5, cloud_id=10599)
            print("\n>>> Testing Scenario 7: Celebrating (dynamic=31, 10 colors, speed=5, cloud_id=10599)")
            celeb_rgb = "FF0000|FF8E00|FFFC00|96FF00|00FFCC|00CDFF|0014FF|8A00FF|D900FF|FF07A9"
            colors = [tuple(bytes.fromhex(c)) for c in celeb_rgb.split("|")]
            value = _effect_payload(31, colors, 0, 5, 10599, update_method=0)
            payload = _command_payload(cloud._user_id, value)
            cloud._publish(serial, (0x02, 0x06), payload, version=0)

        print("\nWaiting 4 seconds for immediate responses...")
        await asyncio.sleep(4)

        print("\nQuerying settings to check updated state...")
        cloud.request_settings(serial)
        await asyncio.sleep(3)

        dev = cloud.devices[serial]
        print(f"\n--- Post-Test State for {serial} ---")
        print(f"Power: {dev.is_on}, Brightness: {dev.brightness}%, Light ID: {dev.light_id}")

        await cloud.async_close()

if __name__ == "__main__":
    scenario = sys.argv[1] if len(sys.argv) > 1 else "1"
    asyncio.run(run_experiment(scenario))
