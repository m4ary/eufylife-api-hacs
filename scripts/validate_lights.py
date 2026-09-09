import asyncio
import getpass
import aiohttp
import sys
import os
import logging
from unittest.mock import MagicMock

# Mock Home Assistant and Voluptuous so the script can run without them
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

# Add custom_components to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from custom_components.eufylife_api.cloud import async_login, EufyLifeLightCloud

async def validate():
    logging.basicConfig(level=logging.WARNING)
    
    email = input("EufyLife Email: ")
    password = getpass.getpass("EufyLife Password: ")
    country = input("Country Code (e.g. US, DE, GB): ") or "US"

    async with aiohttp.ClientSession() as session:
        print("\nLogging in...")
        try:
            auth = await async_login(session, email, password, country)
        except Exception as e:
            print(f"Login failed: {e}")
            return

        print("Login successful! Discovering devices...")
        
        cloud = EufyLifeLightCloud(
            session=session,
            user_id=auth["user_id"],
            user_center_id=auth["user_center_id"],
            user_center_token=auth["user_center_token"],
            openudid="validate-script-id",
            country=country,
            language="en",
            timezone="UTC"
        )
        
        try:
            await cloud.async_start()
        except Exception as e:
            print(f"Discovery failed: {e}")
            return

        if not cloud.devices:
            print("No supported lights found in your account.")
            await cloud.async_close()
            return

        print(f"\nFound {len(cloud.devices)} light(s).")
        print("Connecting to MQTT and fetching status (this may take a few seconds)...")
        
        # Wait up to 10 seconds for MQTT and first status report
        for _ in range(10):
            if cloud.connected and all(d.is_on is not None for d in cloud.devices.values()):
                break
            await asyncio.sleep(1)
            
        device_list = list(cloud.devices.values())
        
        while True:
            print("\n" + "="*40)
            print("DEVICES")
            print("="*40)
            for i, device in enumerate(device_list):
                status = "ON" if device.is_on else "OFF"
                online = "Online" if device.online else "Offline"
                brightness = f"{device.brightness}%" if device.brightness is not None else "??%"
                lamp_info = f", {device.lamp_count} segments" if device.lamp_count else ""
                print(f"{i+1}. {device.name} [{device.model}]")
                print(f"   State: {status}, Brightness: {brightness}, Connectivity: {online}{lamp_info}")
                print(f"   Serial: {device.serial}")
            
            print("\nOptions:")
            print("1-N. Control device")
            print("r.   Refresh status")
            print("q.   Quit")
            
            choice = input("\nChoice: ").strip().lower()
            if choice == 'q':
                break
            if choice == 'r':
                for device in device_list:
                    cloud.request_settings(device.serial)
                print("Refresh requested...")
                await asyncio.sleep(1)
                continue
            
            try:
                idx = int(choice) - 1
                if not (0 <= idx < len(device_list)):
                    raise ValueError()
                device = device_list[idx]
            except ValueError:
                print("Invalid choice.")
                continue

            while True:
                print(f"\n--- Controlling: {device.name} ---")
                print("1. Toggle Power")
                print("2. Set Brightness (0-100)")
                print("3. Set RGB/RGBWW Color")
                print("4. Set Effect (Preset, Speed, Direction)")
                print("5. Set Segmented Colors (DIY)")
                print("b. Back to main menu")
                
                action = input("\nAction: ").strip().lower()
                if action == 'b':
                    break
                
                try:
                    if action == '1':
                        new_state = not device.is_on
                        print(f"Turning {'ON' if new_state else 'OFF'}...")
                        cloud.set_power(device.serial, new_state)
                        await asyncio.sleep(1)
                    elif action == '2':
                        val = input("Enter brightness (0-100): ")
                        brightness = int(val)
                        print(f"Setting brightness to {brightness}%...")
                        cloud.set_power(device.serial, device.is_on, brightness)
                        await asyncio.sleep(1)
                    elif action == '3':
                        if not device.lamp_count:
                            print("Error: Lamp segment count unknown yet. Try refreshing or toggling power first.")
                            continue
                        print("Enter RGB (3 values) or RGBWW (5 values) separated by commas.")
                        val = input("Enter values (e.g., 255,128,0 or 255,0,0,255,0): ")
                        color = tuple(map(int, val.split(',')))
                        if len(color) not in (3, 5):
                            raise ValueError("Need 3 or 5 comma-separated values (0-255)")
                        print(f"Setting color to {color}...")
                        if len(color) == 3:
                            await cloud.async_set_effect(device.serial, rgb_color=color)
                        else:
                            await cloud.async_set_effect(device.serial, rgbww_color=color)
                        print("Success!")
                    elif action == '4':
                        if not device.effects:
                            print("No presets found for this device.")
                            continue
                        effect_list = list(device.effects.keys())
                        print("\nAvailable Presets:")
                        for i, name in enumerate(effect_list):
                            print(f"{i+1}. {name}")
                        e_choice = input("\nSelect preset number: ")
                        e_idx = int(e_choice) - 1
                        effect_name = effect_list[e_idx]
                        
                        speed_val = input("Enter speed (1-10, leave empty for default): ")
                        speed = int(speed_val) if speed_val.strip() else None
                        
                        dir_val = input("Enter direction (0-1, leave empty for default): ")
                        direction = int(dir_val) if dir_val.strip() else None
                        
                        print(f"Setting preset: {effect_name}...")
                        await cloud.async_set_effect(device.serial, effect=effect_name, speed=speed, direction=direction)
                        print("Success!")
                    elif action == '5':
                        if not device.lamp_count:
                            print("Error: Lamp count unknown.")
                            continue
                        print(f"Enter {device.lamp_count} colors. Use R,G,B or R,G,B,W,C for each.")
                        colors = []
                        for i in range(device.lamp_count):
                            val = input(f"Segment {i+1}: ")
                            color = tuple(map(int, val.split(',')))
                            colors.append(color)
                        await cloud.async_set_effect(device.serial, colors=colors)
                        print("Success!")
                    else:
                        print("Invalid action.")
                except Exception as e:
                    print(f"Error: {e}")
                
                # Update local device reference to see changes
                device = cloud.devices[device.serial]

        await cloud.async_close()
        print("\nClosed. Goodbye!")

if __name__ == "__main__":
    try:
        asyncio.run(validate())
    except KeyboardInterrupt:
        pass
