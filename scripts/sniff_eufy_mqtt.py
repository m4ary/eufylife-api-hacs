import asyncio
import os
import sys
import json
import base64
import time
import logging
from unittest.mock import MagicMock

# Mock HomeAssistant modules
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
    _parse_frame,
    _parse_tlvs,
    _xor,
)

LOG_FILE = os.path.abspath("mqtt_sniff.log")

def safe_parse_tlvs(payload: bytes):
    result = []
    offset = 0
    while offset < len(payload):
        if offset + 2 > len(payload):
            break
        tag = payload[offset]
        # Check if tag is 0xAB or length > 255
        length = payload[offset + 1]
        hdr_len = 2
        # If tag is 0xAB, it often uses 2-byte little endian length
        if tag == 0xAB and offset + 3 <= len(payload):
            length = int.from_bytes(payload[offset + 1 : offset + 3], "little")
            hdr_len = 3
        elif length == 0xFF and offset + 3 <= len(payload):
            length = int.from_bytes(payload[offset + 1 : offset + 3], "little")
            hdr_len = 3
        
        offset += hdr_len
        if offset + length > len(payload):
            # Fallback: maybe it was 1-byte length for 0xAB
            if hdr_len == 3:
                offset -= 1
                length = payload[offset - 1]
            if offset + length > len(payload):
                val = payload[offset:]
                result.append((tag, val))
                break
        val = payload[offset : offset + length]
        result.append((tag, val))
        offset += length
    return result

def log_print(msg: str):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] {msg}"
    print(formatted, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(formatted + "\n")
        f.flush()

class SnifferCloud(EufyLifeLightCloud):
    def _on_connect(self, client, userdata, flags, reason_code, properties):
        super()._on_connect(client, userdata, flags, reason_code, properties)
        log_print("MQTT Connected! Subscribing to wildcard topics...")
        client.subscribe("cmd/eufy_life/#", qos=1)
        client.subscribe("synq/eufy_life/#", qos=1)
        log_print("Wildcard subscription active. Waiting for messages...")

    def _on_message(self, client, userdata, message):
        topic = message.topic
        payload_raw = message.payload
        try:
            outer = json.loads(payload_raw.decode("utf-8", "ignore"))
            client_id = outer.get("head", {}).get("client_id", "unknown")
            cmd = outer.get("head", {}).get("cmd", "unknown")
            log_print(f"\n--- MESSAGE ON {topic} from {client_id} (cmd={cmd}) ---")
            
            inner = outer.get("payload")
            if isinstance(inner, str):
                try:
                    inner = json.loads(inner)
                except Exception:
                    pass
            
            # Helper to unwrap recursively
            def unwrap(data, depth=0):
                if depth > 4: return None
                if isinstance(data, bytes):
                    if data.startswith(b"\xff\x09"):
                        return data
                    try:
                        return unwrap(data.decode("utf-8", "ignore"), depth + 1)
                    except Exception:
                        return None
                if isinstance(data, str):
                    if len(data) >= 20 and all(c in "0123456789abcdefABCDEF" for c in data[:20]):
                        try:
                            b = bytes.fromhex(data)
                            if b.startswith(b"\xff\x09"): return b
                        except Exception: pass
                    if data.startswith("{"):
                        try:
                            j = json.loads(data)
                            if isinstance(j, dict) and "data" in j:
                                return unwrap(j["data"], depth + 1)
                        except Exception: pass
                    try:
                        b = base64.b64decode(data, validate=True)
                        if b.startswith(b"\xff\x09"): return b
                        # might be base64-encoded json string
                        if b.startswith(b"{"):
                            j = json.loads(b.decode("utf-8", "ignore"))
                            if isinstance(j, dict) and "data" in j:
                                return unwrap(j["data"], depth + 1)
                    except Exception: pass
                return None

            data_str = inner.get("data") if isinstance(inner, dict) else None
            frame = unwrap(data_str)
            if frame:
                v = frame[5]
                opcode = (frame[7], frame[8])
                body = frame[9:-1]
                chk = frame[-1]
                expected_chk = _xor(frame[:-1])
                valid = "OK" if chk == expected_chk else f"ERR (got {chk:02x}, exp {expected_chk:02x})"
                log_print(f"FRAME: ver={v}, opcode={opcode} ({opcode[0]:02x}{opcode[1]:02x}), len={len(frame)}, chk={valid}")
                log_print(f"RAW HEX: {frame.hex()}")
                
                # Parse tags
                try:
                    tlv_body = body
                    if len(body) > 1 and body[0] == 0x00 and body[1] >= 0x80:
                        tlv_body = body[1:]
                    tlvs = safe_parse_tlvs(tlv_body)
                    for tag, val in tlvs:
                        desc = f"Tag 0x{tag:02X} (len={len(val)}): {val.hex()}"
                        # Try decoding string or int
                        if len(val) == 1:
                            desc += f" [int: {val[0]}]"
                        elif len(val) == 2:
                            desc += f" [int2LE: {int.from_bytes(val, 'little')}]"
                        elif len(val) == 4:
                            desc += f" [int4LE: {int.from_bytes(val, 'little')}]"
                        try:
                            s = val.decode("utf-8")
                            if s.isprintable() and len(s) > 3:
                                desc += f" [str: {s[:120]}...]"
                        except Exception:
                            pass
                        log_print(f"  {desc}")
                except Exception as e:
                    log_print(f"  TLV Parse error: {e}")
            else:
                log_print(f"Non-frame payload: {str(inner)[:300]}")
        except Exception as e:
            log_print(f"Error handling message on {topic}: {e}")

async def run_sniffer():
    email = os.environ.get("YOUR_EMAIL")
    password = os.environ.get("YOUR_PASSWORD")
    country = os.environ.get("YOUR_COUNTRY", "nl").lower()
    
    if not email or not password:
        print("Missing credentials in env (YOUR_EMAIL / YOUR_PASSWORD)")
        sys.exit(1)

    log_print(f"Sniffer starting up for {email} ({country})...")
    async with aiohttp.ClientSession() as session:
        auth = await async_login(session, email, password, country)
        cloud = SnifferCloud(
            session=session,
            user_id=auth["user_id"],
            user_center_id=auth["user_center_id"],
            user_center_token=auth["user_center_token"],
            openudid="sniffer-e10-live",
            country=country,
            language="en",
            timezone="UTC"
        )
        await cloud.async_start()
        log_print("Cloud start called, listening indefinitely...")
        
        while True:
            await asyncio.sleep(1)

if __name__ == "__main__":
    try:
        asyncio.run(run_sniffer())
    except KeyboardInterrupt:
        log_print("Sniffer stopped by user.")
