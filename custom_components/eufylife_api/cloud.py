"""Eufy Life cloud transport used by T8L30 outdoor lights."""

from __future__ import annotations

import asyncio
import base64
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import platform
import re
import secrets
import ssl
import tempfile
import time
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
import paho.mqtt.client as mqtt

from .const import (
    AUTH_API_BASE_URL,
    CLIENT_ID,
    CLIENT_SECRET,
    LIGHT_API_BASE_URL,
    USER_AGENT_VERSION,
)

_LOGGER = logging.getLogger(__name__)

_LIGHT_PRESET_KEY = "444b380e56f3ffad8aeb4c76494c15f9"
_T8L30_MODEL = "T8L30"
_T8L40_MODEL = "T8L40"
_SUPPORTED_MODELS = {_T8L30_MODEL, _T8L40_MODEL}
_GET_SETTINGS = (0x02, 0x00)
_GET_SETTINGS_RESPONSE = (0x0A, 0x00)
_SET_POWER = (0x02, 0x01)
_SET_POWER_RESPONSE = (0x0A, 0x01)
_REPORT_DEVICE_INFO = (0x02, 0x04)
_SET_SCENE = (0x02, 0x02)
_SET_SCENE_RESPONSE = (0x0A, 0x02)
_SET_EFFECT = (0x02, 0x06)
_SET_EFFECT_RESPONSE = (0x0A, 0x06)
_SET_ANIMATION = (0x02, 0x0D)
_SET_ANIMATION_RESPONSE = (0x0A, 0x0D)
_SET_LIGHT_SHOW = (0x02, 0x10)
_SET_LIGHT_SHOW_RESPONSE = (0x0A, 0x10)
_SET_LIGHT_AI = (0x02, 0x11)
_SET_LIGHT_AI_RESPONSE = (0x0A, 0x11)
# App CmdHandler default at 0x5f0858; LightTransportNewCmd uses that default.
_EFFECT_RESPONSE_TIMEOUT = 5


class EufyLifeAuthError(Exception):
    """Raised when Eufy Life rejects account credentials."""


class EufyLifeCloudError(Exception):
    """Raised when the light cloud returns invalid or rejected data."""


@dataclass
class EufyLifeLightDevice:
    """Cloud-discovered E10 light."""

    serial: str
    name: str
    model: str
    account_id: str = ""
    is_on: bool | None = None
    online: bool | None = None
    brightness: int | None = None
    lamp_count: int | None = None
    light_id: int | None = None
    rgb_color: tuple[int, int, int] | None = None
    rgbww_color: tuple[int, int, int, int, int] | None = None
    effect: str | None = None
    speed: int | None = None
    direction: int | None = None
    colors: list[tuple[int, ...]] | None = None
    effects: dict[str, dict[str, Any]] = field(default_factory=dict)


async def async_login(
    session: aiohttp.ClientSession,
    email: str,
    password: str,
    country: str,
) -> dict[str, Any]:
    """Log in using the request emitted by Eufy Life 3.3.12."""
    headers = {
        "Accept": "*/*",
        "User-Agent": f"EufyLife-Android-{USER_AGENT_VERSION}",
        "Category": "Health",
        "Language": "en",
        "Timezone": "UTC",
        "Country": country.upper(),
        "Content-Type": "application/json",
    }
    body = {
        "client_id": CLIENT_ID,
        "client_Secret": CLIENT_SECRET,
        "email": email,
        "password": password,
        "ab": country.lower(),
        "un_subscribe_flag": True,
    }

    async with session.post(
        f"{AUTH_API_BASE_URL}/user/v2/email/login/",
        headers=headers,
        json=body,
        timeout=aiohttp.ClientTimeout(total=30),
    ) as response:
        response.raise_for_status()
        data = await response.json(content_type=None)

    if not isinstance(data, dict):
        raise EufyLifeCloudError("Login returned an invalid response")
    if data.get("res_code") != 1:
        raise EufyLifeAuthError(str(data.get("res_code", "invalid response")))

    access_token = data.get("access_token")
    user_id = data.get("user_id")
    if not isinstance(access_token, str) or not isinstance(user_id, str):
        raise EufyLifeCloudError("Login response is missing account tokens")

    expires_in = data.get("expires_in", 2592000)
    return {
        "access_token": access_token,
        "user_id": user_id,
        "user_center_id": data.get("user_center_id"),
        "user_center_token": data.get("user_center_token"),
        "expires_at": time.time() + int(expires_in),
        "device_id": data.get("device_id"),
        "customer_ids": [
            customer["id"]
            for customer in data.get("customers", [])
            if isinstance(customer, dict) and customer.get("id")
        ],
    }


def _nonce() -> str:
    """Match LightCryptoTools::generateNonce()."""
    return hashlib.md5(secrets.token_hex(16).encode()).hexdigest()


def _signature(message: str, key: str) -> str:
    return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()


def _aes_encrypt(value: str, key: bytes) -> str:
    iv = secrets.token_bytes(16)
    padder = padding.PKCS7(128).padder()
    padded = padder.update(value.encode()) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return base64.b64encode(
        iv + encryptor.update(padded) + encryptor.finalize()
    ).decode()


def _aes_decrypt(value: str, key: bytes) -> str:
    raw = base64.b64decode(value, validate=True)
    if len(raw) < 32 or len(raw) % 16:
        raise EufyLifeCloudError("Invalid encrypted response length")
    decryptor = Cipher(algorithms.AES(key), modes.CBC(raw[:16])).decryptor()
    padded = decryptor.update(raw[16:]) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode()


class _LightCrypto:
    """Small Python equivalent of the app's native LightCryptoUtil."""

    def __init__(self) -> None:
        self._private_key = ec.generate_private_key(ec.SECP256R1())
        numbers = self._private_key.public_key().public_numbers()
        self._public_key = f"04{numbers.x:064X}{numbers.y:064X}"
        ident_material = (
            f"{_LIGHT_PRESET_KEY}ident_salt{int(time.time() * 1000)}{_nonce()}"
        )
        self._key_ident = hashlib.md5(ident_material.encode()).hexdigest()
        self._key: bytes | None = None
        self._security_key: str | None = None

    async def async_exchange(
        self, session: aiohttp.ClientSession, base_url: str
    ) -> None:
        """Exchange P-256 public keys with the production light service."""
        preset = bytes.fromhex(_LIGHT_PRESET_KEY)
        encrypted_key = _aes_encrypt(self._public_key, preset)
        timestamp = str(int(time.time()))
        nonce = _nonce()
        headers = {
            "App-name": "eufy_life",
            "Content-Type": "application/json",
            "X-Encryption-Info": "algo_ecdh",
            "X-Request-Ts": timestamp,
            "X-Request-Once": nonce,
            "X-Key-Ident": self._key_ident,
            "X-Signature": _signature(
                f"{timestamp}+{nonce}+{encrypted_key}", _LIGHT_PRESET_KEY
            ),
        }
        async with session.post(
            f"{base_url}/openapi/oauth/key/exchange",
            headers=headers,
            json={"client_public_key": encrypted_key},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            response.raise_for_status()
            result = await response.json(content_type=None)

        if not isinstance(result, dict) or result.get("code") != 0:
            raise EufyLifeCloudError("Light public-key exchange was rejected")
        data = result.get("data")
        if not isinstance(data, dict):
            raise EufyLifeCloudError("Light public-key response is missing data")
        server_key = data.get("server_public_key")
        server_signature = data.get("signature")
        if not isinstance(server_key, str) or not isinstance(server_signature, str):
            raise EufyLifeCloudError("Light public-key response is incomplete")
        expected = _signature(f"{timestamp}+{nonce}+{server_key}", _LIGHT_PRESET_KEY)
        if not hmac.compare_digest(server_signature.lower(), expected):
            raise EufyLifeCloudError("Invalid light public-key signature")

        public_key = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(), bytes.fromhex(_aes_decrypt(server_key, preset))
        )
        shared_key = self._private_key.exchange(ec.ECDH(), public_key)[:16]
        self._key = shared_key
        self._security_key = shared_key.hex()

    def encrypt(self, value: str) -> tuple[str, str, str, dict[str, str]]:
        """Encrypt and sign one AIoT request body."""
        if self._key is None or self._security_key is None:
            raise EufyLifeCloudError("Light public key has not been exchanged")
        timestamp = str(int(time.time()))
        nonce = _nonce()
        encrypted = _aes_encrypt(value, self._key)
        return (
            encrypted,
            timestamp,
            nonce,
            {
                "X-Replay-Info": "replay",
                "X-Request-Ts": timestamp,
                "X-Request-Once": nonce,
                "X-Encryption-Info": "algo_ecdh",
                "X-Key-Ident": self._key_ident,
                "X-Signature": _signature(
                    f"{timestamp}+{nonce}+{encrypted}", self._security_key
                ),
            },
        )

    def decrypt(
        self,
        value: str,
        request_timestamp: str,
        request_nonce: str,
    ) -> str:
        """Authenticate and decrypt one AIoT response body."""
        if self._key is None or self._security_key is None:
            raise EufyLifeCloudError("Light public key has not been exchanged")
        response = json.loads(value)
        if not isinstance(response, dict) or response.get("code") != 0:
            raise EufyLifeCloudError("Invalid encrypted light response")
        encrypted = response.get("data")
        signature = response.get("signature")
        if not isinstance(encrypted, str):
            raise EufyLifeCloudError("Encrypted light response has no data")
        if not isinstance(signature, str) or not signature:
            raise EufyLifeCloudError("Encrypted light response has no signature")
        expected = _signature(
            f"{request_timestamp}+{request_nonce}+{encrypted}", self._security_key
        )
        if not hmac.compare_digest(signature.lower(), expected):
            raise EufyLifeCloudError("Invalid encrypted light response signature")
        return _aes_decrypt(encrypted, self._key)


def _tlv(tag: int, value: bytes) -> bytes:
    if len(value) > 255:
        raise ValueError("E10 TLV value is too long")
    return bytes((tag, len(value))) + value


def _tlv_long(tag: int, value: bytes) -> bytes:
    """TLV with 2-byte little-endian length."""
    return bytes([tag]) + len(value).to_bytes(2, "little") + value


def _frame(opcode: tuple[int, int], payload: bytes, version: int = 0) -> bytes:
    frame = bytearray((0xFF, 0x09))
    frame.extend((len(payload) + 10).to_bytes(2, "little"))
    frame.extend((0x03, version, 0x02, *opcode))
    frame.extend(payload)
    frame.append(0)
    frame[-1] = _xor(frame[:-1])
    return bytes(frame)


def _xor(value: bytes | bytearray) -> int:
    result = 0
    for byte in value:
        result ^= byte
    return result


def _command_payload(user_id: str, value: bytes) -> bytes:
    user = user_id.encode()
    return _tlv(0xA1, int(time.time()).to_bytes(4, "little")) + _tlv(0xA2, user) + value


def _effect_payload(
    light_id: int,
    colors: list[tuple[int, ...]],
    direction: int,
    speed: int,
    cloud_id: int | None,
    update_method: int = 0,
) -> bytes:
    """LightTransportNewCmd (0xa7cd78), using native RGBWC channels."""
    if not 0 <= light_id <= 65535 or not 0 <= direction <= 65535:
        raise EufyLifeCloudError("Invalid light effect ID or direction")
    if not 1 <= speed <= 10 or not colors:
        raise EufyLifeCloudError("Invalid light effect speed or empty palette")
    for color in colors:
        if len(color) not in (3, 5) or any(
            type(c) is not int or not 0 <= c <= 255 for c in color
        ):
            raise EufyLifeCloudError("Color channels must be integers between 0 and 255 (3 or 5 channels)")
    indices = b""
    if 20000 <= light_id < 30000:
        grouped: dict[tuple[int, ...], list[int]] = {}
        for index, color in enumerate(colors):
            grouped.setdefault(color, []).append(index)
        indices = b"".join(bytes([len(slots), *slots]) for slots in grouped.values())
        colors = list(grouped)
    # palette: direct RGBWC entries; port model/firmware calibration for app-identical whites.
    palette_data = []
    for color in colors:
        if len(color) == 3:
            palette_data.extend((*color, 0, 0))
        else:
            palette_data.extend(color)
    palette = bytes([len(colors)]) + bytes(palette_data)
    value = (
        _tlv(0xA3, light_id.to_bytes(2, "little"))
        + _tlv(0xA4, direction.to_bytes(2, "little"))
        + _tlv(0xA5, bytes([speed]))
        + _tlv(0xA6, palette)
    )
    if 20000 <= light_id < 30000:
        value += _tlv(0xA7, indices)
    # Global brightness is controlled separately by SetUpDeviceCmd, not multiplied twice.
    value += _tlv(0xA8, b"\x64") + _tlv(0xA9, bytes(5)) + _tlv(0xAA, b"\x00")
    if cloud_id is not None:
        if not 0 <= cloud_id <= 0xFFFFFFFF:
            raise EufyLifeCloudError("Invalid cloud preset ID")
        value += _tlv(0xAC, cloud_id.to_bytes(4, "little"))
    return value + _tlv(0xAE, b"\x00") + _tlv(0xB0, bytes([update_method]))


_KNOWN_COLOR_MAP = {
    "ff4d6a": bytes.fromhex("ff01130500"),
    "ffa36a": bytes.fromhex("ff00146200"),
    "ff6a8a": bytes.fromhex("ff01280c00"),
    "a36aff": bytes.fromhex("8701ff0011"),
    "6affff": bytes.fromhex("03ff740017"),
    "a8ff59": bytes.fromhex("02ff011701"),
    "ff5157": bytes.fromhex("ff010b0600"),
    "ff6c5b": bytes.fromhex("ff000b0d00"),
    "ffce3d": bytes.fromhex("ffa1000b00"),
    "0ff3ff": bytes.fromhex("01ff85000e"),
    "99e2ff": bytes.fromhex("04ffc30038"),
    "a35bff": bytes.fromhex("9600ff000b"),
    "fc5bb2": bytes.fromhex("fc01470800"),
    "ff9a03": bytes.fromhex("ff56000000"),
    "ff9f85": bytes.fromhex("ff01404e00"),
    "ffde93": bytes.fromhex("13ff059d34"),
}

_T8L40_VERIFIED_ANIMATION_LAYERS: dict[int, dict[str, Any]] = {
    10854: {  # Guy Fawkes Night
        "speed": 54,
        "exec_mode": 3,
        "layers": [
            bytes.fromhex("0164640001010082010005ff01130500ff00146200ff01280c008701ff001103ff740017000000000000000300000900046410020c"),
            bytes.fromhex("0014644b0101000103010503ff7400178701ff0011ff01280c0002ff011701ff0113050061010202000000010002"),
        ],
    },
    10599: {  # Celebrating
        "speed": 30,
        "exec_mode": 0,
        "layers": [
            bytes.fromhex("0014640001010080000105ff010b0600ff000b0d00ffa1000b0001ff85000e04ffc3003864010502000000000001"),
            bytes.fromhex("0019640001010080000205ff010b0600ff000b0d00ffa1000b0001ff85000e04ffc3003803640901010502ff01020001"),
        ],
    },
    10589: {  # Party
        "speed": 28,
        "exec_mode": 0,
        "layers": [
            bytes.fromhex("00246400010100800001059600ff000bfc01470800ff56000000ff01404e0013ff059d3464010502000000000100"),
        ],
    },
}


def _encode_animation_layer(layer: dict[str, Any]) -> bytes:
    pri = int(layer.get("layer_priority", 0)) & 0xFF
    spd = int(layer.get("layer_speed", 100)) & 0xFF
    rng = layer.get("layer_range", [0, 100])
    r_hi = int(rng[1]) & 0xFF if len(rng) > 1 else 100
    r_lo = int(rng[0]) & 0xFF if len(rng) > 0 else 0
    i_type = int(layer.get("interval_type", 1)) & 0xFF
    i_val = int(layer.get("interval_value", 1)) & 0xFF
    exec_param = int(layer.get("layer_execution_parameter", 1)) & 0xFFFF
    post_status = int(layer.get("light_effect_post_cycle_status", 0)) & 0xFF
    layer_type = int(layer.get("current_layer_type", 1)) & 0xFF

    header = (
        bytes([pri, spd, r_hi, r_lo, i_type, i_val])
        + exec_param.to_bytes(2, "big")
        + bytes([post_status, layer_type])
    )

    raw_colors = layer.get("colors", "")
    color_list = raw_colors.split("|") if isinstance(raw_colors, str) else []
    color_bytes = bytearray([len(color_list)])
    for c_str in color_list:
        c_clean = c_str.strip().lower()
        if c_clean in _KNOWN_COLOR_MAP:
            color_bytes.extend(_KNOWN_COLOR_MAP[c_clean])
        elif len(c_clean) == 6:
            r = int(c_clean[0:2], 16)
            g = int(c_clean[2:4], 16)
            b = int(c_clean[4:6], 16)
            color_bytes.extend([r, g, b, 0, 0])
        else:
            color_bytes.extend([255, 255, 255, 0, 0])

    trailing = bytearray()
    if layer_type == 0:
        trailing.extend([
            int(layer.get("color_fill_mode", 0)) & 0xFF,
            int(layer.get("color_pick_mode", 0)) & 0xFF,
            int(layer.get("flow_direction", 0)) & 0xFF,
            int(layer.get("direction_change_mode", 0)) & 0xFF,
            int(layer.get("insert_block_mode", 0)) & 0xFF,
            0,
            int(layer.get("insert_block_range", 0)) & 0xFF,
            int(layer.get("insert_black_block_mode", 0)) & 0xFF,
            0,
            int(layer.get("insert_black_block_range", 0)) & 0xFF,
            int(layer.get("insert_black_block_position_mode", 0)) & 0xFF,
        ])
        b_var = int(layer.get("brightness_variation_type", 0)) & 0xFF
        b_rng = layer.get("brightness_range", [0, 100])
        b_hi = int(b_rng[1]) & 0xFF if len(b_rng) > 1 else 100
        b_lo = int(b_rng[0]) & 0xFF if len(b_rng) > 0 else 0
        cycle = int(layer.get("light_effect_cycle_method", 0)) & 0xFF
        param = int(layer.get("execution_parameter", 0)) & 0xFF
        trailing.extend([b_var, b_hi, b_lo, cycle, param])
    elif layer_type == 1:
        b_val = int(layer.get("brightness_value", 100)) & 0xFF
        disp = int(layer.get("display_mode", 1)) & 0xFF
        q_rng = int(layer.get("color_quantity_range", len(color_list))) & 0xFF
        trans = int(layer.get("transition_mode", 2)) & 0xFF
        switch = int(layer.get("color_switch_mode", 0)) & 0xFF
        seq = int(layer.get("color_pick_sequence", 0)) & 0xFF
        cycle = int(layer.get("light_effect_cycle_method", 0)) & 0xFF
        param = int(layer.get("execution_parameter", 0)) & 0xFF
        trailing.extend([b_val, disp, q_rng, trans, switch, seq, 0, 0, cycle, param])
    elif layer_type == 2:
        b_var = int(layer.get("brightness_variation_type", 0)) & 0xFF
        b_rng = layer.get("brightness_range", [0, 100])
        b_hi = int(b_rng[1]) & 0xFF if len(b_rng) > 1 else 100
        b_lo = int(b_rng[0]) & 0xFF if len(b_rng) > 0 else 0
        c_count = int(layer.get("blink_cycle_count", 1)) & 0xFF
        pos = int(layer.get("blink_position_mode", 1)) & 0xFF
        b_int = layer.get("blink_interval", [2, 5])
        i_hi = int(b_int[1]) & 0xFF if len(b_int) > 1 else 5
        i_lo = int(b_int[0]) & 0xFF if len(b_int) > 0 else 2
        qty = int(layer.get("blink_quantity", 255)) & 0xFF
        async_flag = int(layer.get("blink_asynchrony", 1)) & 0xFF
        c_switch = int(layer.get("blink_color_switch_mode", 2)) & 0xFF
        cycle = int(layer.get("light_effect_cycle_method", 0)) & 0xFF
        param = int(layer.get("execution_parameter", 0)) & 0xFF
        trailing.extend([b_var, b_hi, b_lo, c_count, pos, i_hi, i_lo, qty, async_flag, c_switch, cycle, param])

    return header + bytes(color_bytes) + bytes(trailing)


def _build_t8l40_animation_payload(
    cloud_id: int,
    params: str | dict[str, Any] | None = None,
    speed: int | None = None,
) -> bytes:
    """Build the authentic binary multi-layer animation payload for T8L40 (Opcode 020D)."""
    if cloud_id in _T8L40_VERIFIED_ANIMATION_LAYERS:
        data = _T8L40_VERIFIED_ANIMATION_LAYERS[cloud_id]
        eff_speed = speed if speed is not None else data["speed"]
        body = (
            _tlv(0xA3, cloud_id.to_bytes(4, "little"))
            + _tlv(0xA4, bytes([eff_speed & 0xFF]))
            + _tlv(0xA5, bytes([len(data["layers"]) & 0xFF]))
            + _tlv(0xA6, bytes([data["exec_mode"] & 0xFF]))
            + _tlv(0xA8, b"\x00")
        )
        for i, l_bytes in enumerate(data["layers"]):
            tag = 0xA9 + i
            body += bytes([tag, len(l_bytes)]) + l_bytes
        return body

    if isinstance(params, str):
        try:
            params = json.loads(params)
        except Exception:
            params = {}
    if not isinstance(params, dict):
        params = {}

    eff_speed = speed if speed is not None else int(params.get("light_effect_speed") or 50)
    layers = params.get("layer", [])
    exec_mode = int(params.get("layer_execution_mode") or 0)
    body = (
        _tlv(0xA3, cloud_id.to_bytes(4, "little"))
        + _tlv(0xA4, bytes([eff_speed & 0xFF]))
        + _tlv(0xA5, bytes([len(layers) & 0xFF]))
        + _tlv(0xA6, bytes([exec_mode & 0xFF]))
        + _tlv(0xA8, b"\x00")
    )
    for i, l in enumerate(layers):
        l_bytes = _encode_animation_layer(l)
        tag = 0xA9 + i
        body += bytes([tag, len(l_bytes)]) + l_bytes
    return body


def _parse_effects(data: Any) -> dict[str, dict[str, Any]]:
    """Expose only catalog entries supported by the recovered classic serializer."""
    result = {}
    if not isinstance(data, dict) or not isinstance(data.get("list"), list):
        raise EufyLifeCloudError("Invalid light preset catalog")
    for category in data["list"]:
        if not isinstance(category, dict) or not isinstance(
            category.get("scene_info"), list
        ):
            raise EufyLifeCloudError("Invalid preset category")
        for scene in category.get("scene_info", []):
            if not isinstance(scene, dict) or not isinstance(scene.get("light"), list):
                raise EufyLifeCloudError("Invalid preset scene")
            for preset in scene.get("light", []):
                try:
                    if not isinstance(preset, dict):
                        continue
                    name = preset.get("name", "Unknown")
                    palette = preset.get("rgb_hex")
                    if (
                        isinstance(name, str)
                        and name
                        and isinstance(palette, str)
                        and re.fullmatch(
                            r"[0-9a-fA-F]{6}(?:\|[0-9a-fA-F]{6})*", palette
                        )
                        and "dynamic" in preset
                    ):
                        colors = [tuple(bytes.fromhex(c)) for c in palette.split("|")]
                        params = {
                            "light_id": int(preset["light_id"]),
                            "dynamic": int(preset["dynamic"]),
                            "direction": int(preset.get("dynamic_direct", 0)),
                            "speed": int(preset.get("speed", 1)),
                            "colors": colors,
                        }
                        if "scene_id" in scene:
                            params["scene_id"] = int(scene["scene_id"])
                        if preset.get("params"):
                            params["params"] = preset["params"]
                        if preset.get("params_version"):
                            params["params_version"] = int(preset["params_version"])
                        _effect_payload(
                            params["dynamic"],
                            colors,
                            params["direction"],
                            params["speed"],
                            params["light_id"],
                        )
                        result[name] = params
                        continue

                    params_version = int(preset.get("params_version") or 0)
                    if params_version > 0 and preset.get("params"):
                        params = {
                            "params_version": params_version,
                            "params": preset.get("params"),
                            "light_id": int(preset.get("light_id") or 0),
                            "scene_id": int(scene.get("scene_id") or 0),
                        }
                        result[name] = params
                except (KeyError, TypeError, ValueError, EufyLifeCloudError):
                    _LOGGER.debug("Skipping unsupported light preset")
    return result


def _parse_frame(frame: bytes) -> tuple[tuple[int, int], bytes]:
    if len(frame) < 10 or frame[:2] != b"\xff\x09":
        raise ValueError("Invalid E10 frame header")
    if frame[4] != 3 or frame[5] not in (0, 1) or frame[6] != 2:
        raise ValueError("Invalid E10 frame protocol")
    if int.from_bytes(frame[2:4], "little") != len(frame):
        raise ValueError("Invalid E10 frame length")
    if _xor(frame[:-1]) != frame[-1]:
        raise ValueError("Invalid E10 frame checksum")
    return (frame[7], frame[8]), frame[9:-1]


def _parse_tlvs(payload: bytes) -> dict[int, bytes]:
    result: dict[int, bytes] = {}
    offset = 0
    while offset < len(payload):
        if offset + 2 > len(payload):
            raise ValueError("Truncated E10 TLV header")
        tag, length = payload[offset : offset + 2]
        offset += 2
        if offset + length > len(payload):
            raise ValueError("Truncated E10 TLV value")
        result[tag] = payload[offset : offset + length]
        offset += length
    return result


class EufyLifeLightCloud:
    """Discover and control E10 lights through Eufy's app cloud."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        user_id: str,
        user_center_id: str,
        user_center_token: str,
        openudid: str,
        country: str,
        language: str,
        timezone: str,
    ) -> None:
        self._session = session
        self._user_id = user_id
        self._user_center_id = user_center_id
        self._user_center_token = user_center_token
        self._openudid = openudid
        self._country = country.upper()
        self._language = language
        self._timezone = timezone
        self._base_url = LIGHT_API_BASE_URL
        self._crypto = _LightCrypto()
        self._loop = asyncio.get_running_loop()
        self._mqtt: mqtt.Client | None = None
        self._mqtt_files: tempfile.TemporaryDirectory[str] | None = None
        self._listeners: dict[str, set[Callable[[], None]]] = defaultdict(set)
        self.devices: dict[str, EufyLifeLightDevice] = {}
        self.connected = False
        self._effect_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._effect_replies: dict[str, asyncio.Future[None]] = {}
        self._msg_seq = 0

    async def async_start(self) -> None:
        """Discover lights and start the app's certificate-authenticated MQTT link."""
        async with self._session.post(
            f"{LIGHT_API_BASE_URL}/passport/estimate_domain",
            headers={"App-Name": "eufy_life"},
            json={"ab": self._country, "mode": 1},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            response.raise_for_status()
            region = await response.json(content_type=None)
        if not isinstance(region, dict) or region.get("code") != 0:
            raise EufyLifeCloudError("Light region lookup was rejected")
        data = region.get("data")
        domain = data.get("domain") if isinstance(data, dict) else None
        if not isinstance(domain, str):
            raise EufyLifeCloudError("Light region response is missing its domain")
        domain = domain.removeprefix("https://")
        if (
            not domain.endswith(".eufylife.com")
            or urlsplit(f"https://{domain}").hostname != domain
        ):
            raise EufyLifeCloudError("Invalid light region domain")
        self._base_url = f"https://{domain}"
        await self._crypto.async_exchange(self._session, self._base_url)
        discovered = await self._async_post("/app/devicerelation/get_device_list", {})
        relations = (
            discovered.get("devices", []) if isinstance(discovered, dict) else []
        )
        for relation in relations or []:
            if not isinstance(relation, dict):
                continue
            raw = relation.get("device", relation)
            if not isinstance(raw, dict) or raw.get("device_model") not in _SUPPORTED_MODELS:
                continue
            serial = raw.get("device_sn")
            if not isinstance(serial, str) or not serial:
                continue
            member = raw.get("member")
            account_id = self._user_id
            if isinstance(member, dict) and member.get("member_type") != 2:
                account_id = member.get("admin_user_id")
                if not isinstance(account_id, str) or not account_id:
                    raise EufyLifeCloudError("Shared light is missing its owner ID")
            model = str(raw.get("device_model"))
            self.devices[serial] = EufyLifeLightDevice(
                serial=serial,
                name=str(raw.get("device_name") or "Eufy Light"),
                model=model,
                account_id=account_id,
            )

        if not self.devices:
            return
        for device in self.devices.values():
            try:
                device.effects = _parse_effects(
                    await self._async_post(
                        "/app/light/lightmode/list",
                        {"sns": [device.serial], "light_type": None, "scene_id": None},
                    )
                )
            except (
                aiohttp.ClientError,
                TimeoutError,
                ValueError,
                TypeError,
                EufyLifeCloudError,
            ):
                _LOGGER.warning(
                    "Light presets unavailable; power and RGB remain supported"
                )
        mqtt_info = await self._async_post("/app/devicemanage/get_user_mqtt_info", {})
        if not isinstance(mqtt_info, dict):
            raise EufyLifeCloudError("MQTT response is missing credentials")
        await asyncio.to_thread(self._start_mqtt, mqtt_info)

    async def _async_post(self, path: str, body: dict[str, Any]) -> Any:
        raw_body = json.dumps(body, separators=(",", ":"))
        encrypted, request_timestamp, request_nonce, crypto_headers = (
            self._crypto.encrypt(raw_body)
        )
        timestamp = str(int(time.time()))
        headers = {
            "X-Request_Ts": timestamp,
            "X-Request_Once": self._openudid,
            "Unique-Sign": self._openudid,
            "Gtoken": hashlib.md5(self._user_center_id.encode()).hexdigest(),
            "X-Auth-Token": self._user_center_token,
            "X-Custom": '{"light_effect":"new"}',
            "App-Name": "eufy_life",
            "Model-Type": "PHONE",
            "Openudid": self._openudid,
            "Content-Type": "application/json",
            "Timezone": self._timezone,
            "Os-Type": "android",
            "Os-Version": platform.release(),
            "Phone-Model": platform.machine(),
            "App-Version": USER_AGENT_VERSION,
            "Country": self._country,
            "Language": self._language,
            "ab_code": self._country,
            "Encrypt-Algorithm": "algorithm_ecdh",
            **crypto_headers,
        }
        async with self._session.post(
            f"{self._base_url}{path}",
            headers=headers,
            data=encrypted,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            response.raise_for_status()
            response_body = await response.text()
            response_headers = response.headers

        if response_headers.get("encrypt-algorithm") == "algorithm_ecdh":
            response_body = self._crypto.decrypt(
                response_body,
                request_timestamp,
                request_nonce,
            )
        result = json.loads(response_body)
        if not isinstance(result, dict) or result.get("code") != 0:
            code = (
                result.get("code") if isinstance(result, dict) else "invalid response"
            )
            raise EufyLifeCloudError(f"Light API {path} failed: {code}")
        return result.get("data")

    def _start_mqtt(self, info: dict[str, Any]) -> None:
        endpoint = _required_string(info, "endpoint_addr")
        certificate = _required_string(info, "certificate_pem")
        private_key = _required_string(info, "private_key")

        files = tempfile.TemporaryDirectory(prefix="eufylife-mqtt-")
        certificate_path = Path(files.name, "client.pem")
        key_path = Path(files.name, "client.key")
        certificate_path.write_text(certificate)
        key_path.write_text(private_key)
        os.chmod(key_path, 0o600)
        # Eufy's supplied legacy CA fails strict X.509; the broker uses a public CA.
        context = ssl.create_default_context()
        context.load_cert_chain(certificate_path, key_path)

        client_id = f"android-eufy_life-{self._user_id}-{self._openudid}"
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            clean_session=True,
            protocol=mqtt.MQTTv311,
        )
        client.tls_set_context(context)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.connect_async(endpoint, 8883, 60)
        self._mqtt_files = files
        self._mqtt = client
        client.loop_start()

    def _on_connect(
        self,
        client: mqtt.Client,
        _userdata: Any,
        _flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        _properties: mqtt.Properties | None,
    ) -> None:
        if reason_code != 0:
            _LOGGER.error("Eufy Life MQTT connection rejected: %s", reason_code)
            return
        self.connected = True
        for device in self.devices.values():
            prefix = f"eufy_life/{device.model}/{device.serial}"
            for topic in (
                f"cmd/{prefix}/req",
                f"cmd/{prefix}/res",
                f"cmd/{prefix}/app/req",
                f"cmd/{prefix}/app/res",
                f"cmd/{prefix}/app/ota/res",
                f"synq/{prefix}/state_info",
            ):
                client.subscribe(topic, qos=1)
            self.request_settings(device.serial)
        self._notify()

    def _on_disconnect(
        self,
        _client: mqtt.Client,
        _userdata: Any,
        _disconnect_flags: mqtt.DisconnectFlags,
        reason_code: mqtt.ReasonCode,
        _properties: mqtt.Properties | None,
    ) -> None:
        self.connected = False
        if reason_code != 0:
            _LOGGER.warning("Eufy Life MQTT disconnected: %s", reason_code)
        self._notify()

    def _on_message(
        self, _client: mqtt.Client, _userdata: Any, message: mqtt.MQTTMessage
    ) -> None:
        _LOGGER.info("MQTT message received on %s: %s", message.topic, message.payload[:200])
        try:
            parts = message.topic.split("/")
            if len(parts) < 5 or (serial := parts[3]) not in self.devices:
                return
            outer = json.loads(message.payload.decode())
            inner = outer.get("payload")
            if isinstance(inner, str):
                inner = json.loads(inner)
            if not isinstance(inner, dict):
                _LOGGER.info("MQTT payload is not a dict")
                return
            if message.topic.endswith("/state_info"):
                status = inner.get("status")
                if isinstance(status, bool):
                    self.devices[serial].online = status
                    self._notify(serial)
                return

            def unwrap(data: Any) -> bytes | None:
                if isinstance(data, bytes):
                    if data.startswith(b"\xff\x09"):
                        return data
                    try:
                        return unwrap(data.decode())
                    except Exception:
                        return None
                if not isinstance(data, str):
                    return None
                if not data:
                    return None

                # Try Hex
                if len(data) >= 20 and all(c in "0123456789abcdefABCDEF" for c in data[:20]):
                    try:
                        b = bytes.fromhex(data)
                        if b.startswith(b"\xff\x09"):
                            return b
                    except Exception:
                        pass

                # Try JSON
                if data.startswith("{"):
                    try:
                        j = json.loads(data)
                        if isinstance(j, dict) and "data" in j:
                            return unwrap(j["data"])
                    except Exception:
                        pass

                # Try Base64
                try:
                    b = base64.b64decode(data, validate=True)
                    return unwrap(b)
                except Exception:
                    pass

                return None

            frame_bytes = unwrap(inner.get("data"))
            if not frame_bytes:
                _LOGGER.info("MQTT message on %s could not be unwrapped to an E10 frame: %s", message.topic, inner)
                return

            _LOGGER.info("Unwrapped frame bytes for %s (len=%s): %s", serial, len(frame_bytes), frame_bytes.hex())
            opcode, payload = _parse_frame(frame_bytes)
            self._handle_frame(serial, opcode, payload)
        except Exception:  # MQTT input is an external trust boundary.
            _LOGGER.exception("Discarding an invalid Eufy Life MQTT message")

    def _handle_frame(
        self, serial: str, opcode: tuple[int, int], payload: bytes
    ) -> None:
        _LOGGER.info("Frame received for %s: opcode=%s, payload=%s", serial, opcode, payload.hex())

        # T8L40 echoes the command opcode (02, 04) on success or status reports
        if self.devices[serial].model == _T8L40_MODEL and opcode == (0x02, 0x04):
            self._loop.call_soon_threadsafe(self._complete_effect, serial, 0)

        if opcode == (0x02, 0x00):
            self._loop.call_soon_threadsafe(self._complete_effect, serial, 0)
            return

        if opcode in (
            _SET_EFFECT_RESPONSE,
            _SET_ANIMATION_RESPONSE,
            _SET_LIGHT_SHOW_RESPONSE,
            _SET_LIGHT_AI_RESPONSE,
            _SET_SCENE_RESPONSE,
        ):
            # Captured 00 a1 01 00: envelope success plus command result TLV A1.
            # Some responses (like SET_SCENE) are just a single byte 00.
            if not payload:
                raise ValueError("Effect response is missing status")
            status = payload[0]
            if status == 0 and len(payload) > 1:
                tlvs = _parse_tlvs(payload[1:])
                result = tlvs.get(0xA1)
                if result is not None and len(result) == 1:
                    status = result[0]
            # result: 00 = Success, anything else = Failure
            self._loop.call_soon_threadsafe(self._complete_effect, serial, status)
            return
        if opcode in (_GET_SETTINGS_RESPONSE, _SET_POWER_RESPONSE):
            if not payload:
                raise ValueError("E10 response is missing its status")
            if payload[0] != 0:
                _LOGGER.warning("Eufy Life command %s rejected: %s", opcode, payload[0])
                return
            payload = payload[1:]
        if opcode in (_GET_SETTINGS_RESPONSE, _REPORT_DEVICE_INFO):
            values = _parse_tlvs(payload)
            power = values.get(0xA1)
            brightness = values.get(0xA2)
            if brightness is not None and (len(brightness) != 1 or brightness[0] > 100):
                raise ValueError("Invalid E10 brightness percentage")
            if power is not None:
                self.devices[serial].is_on = int.from_bytes(power, "little") == 1
            if brightness is not None:
                self.devices[serial].brightness = brightness[0]
            device = self.devices[serial]
            if (count := values.get(0xA3)) is not None:
                if len(count) not in (1, 2):
                    raise ValueError("Invalid light count")
                device.lamp_count = int.from_bytes(count, "little")
            if (mode := values.get(0xA4)) is not None:
                # Settings use uint16; unsolicited reports use uint32 (captured).
                if len(mode) not in (2, 4):
                    raise ValueError("Invalid light effect ID")
                light_id = int.from_bytes(mode, "little")
                if light_id != device.light_id:
                    device.rgb_color = None
                    device.effect = None
                    device.colors = []
                device.light_id = light_id
            cloud_id = values.get(0xA6)
            if cloud_id and len(cloud_id) == 4:
                cid = int.from_bytes(cloud_id, "little")
                if cid:
                    for eff_name, eff_p in device.effects.items():
                        if eff_p.get("light_id") == cid:
                            device.effect = eff_name
                            break
            self._notify(serial)
            return
        if opcode == _SET_POWER_RESPONSE:
            self.request_settings(serial)

    def request_settings(self, serial: str) -> None:
        payload = _command_payload(
            self._user_id, _tlv(0xA3, (0x1FF).to_bytes(4, "little"))
        )
        self._publish(serial, _GET_SETTINGS, payload)

    def _complete_effect(self, serial: str, status: int) -> None:
        future = self._effect_replies.get(serial)
        if future is not None and not future.done():
            if status:
                future.set_exception(
                    EufyLifeCloudError(f"Light rejected effect: {status}")
                )
            else:
                future.set_result(None)

    async def async_handshake(self, serial: str) -> None:
        """Perform the iOS-style session handshake (Opcode 0200)."""
        # A1: Timestamp, A2: UserID, A3: ff010000 (Session Mask)
        value = _tlv(0xA3, bytes.fromhex("ff010000"))
        payload = _command_payload(self._user_id, value)

        async with self._effect_locks[serial]:
            future = self._loop.create_future()
            self._effect_replies[serial] = future
            try:
                _LOGGER.info("Performing Session Handshake for %s", serial)
                # Handshake always uses Version 0
                self._publish(serial, (0x02, 0x00), payload, version=0)
                await asyncio.wait_for(future, timeout=_EFFECT_RESPONSE_TIMEOUT)
                _LOGGER.info("Handshake Success for %s", serial)
            except asyncio.TimeoutError:
                _LOGGER.warning("Handshake timed out for %s, continuing anyway", serial)
            finally:
                self._effect_replies.pop(serial, None)

    async def async_set_effect(
        self,
        serial: str,
        rgb_color: tuple[int, int, int] | None = None,
        rgbww_color: tuple[int, int, int, int, int] | None = None,
        effect: str | None = None,
        colors: list[tuple[int, ...]] | None = None,
        speed: int | None = None,
        direction: int | None = None,
        params: str | None = None,
        use_ai_opcode: bool = False,
        refresh: bool = True,
    ) -> None:
        """Wait for the device result before remembering the selected palette."""
        device = self.devices[serial]

        # For T8L40, ensure a handshake has occurred for this connection
        if device.model == _T8L40_MODEL and effect is not None:
            await self.async_handshake(serial)
            await asyncio.sleep(0.5)

        if (rgb_color is not None or rgbww_color is not None) and effect is not None:
            raise EufyLifeCloudError("Choose either a color or a preset")

        # Determine parameters
        target_speed = speed if speed is not None else 1
        target_direction = direction if direction is not None else 0
        target_cloud_id = None
        light_id = None
        target_colors = None
        target_params = params

        protocol_version = 0
        use_version_1 = False
        if effect is not None:
            if effect not in device.effects:
                raise EufyLifeCloudError(f"Unsupported light preset: {effect}")
            p = device.effects[effect]
            target_cloud_id = p.get("light_id")

            if device.model == _T8L40_MODEL and (
                target_cloud_id in _T8L40_VERIFIED_ANIMATION_LAYERS or "params" in p
            ):
                opcode = _SET_ANIMATION
                value = _build_t8l40_animation_payload(
                    target_cloud_id or 0, p.get("params"), speed
                )
                payload = _command_payload(self._user_id, value)
            elif "dynamic" in p:
                opcode = _SET_EFFECT
                light_id = p["dynamic"]
                target_colors = p["colors"]
                if speed is None:
                    target_speed = p["speed"]
                if direction is None:
                    target_direction = p["direction"]
                value = _effect_payload(
                    light_id, target_colors, target_direction, target_speed, target_cloud_id
                )
                payload = _command_payload(self._user_id, value)
            elif "params" in p:
                opcode = _SET_LIGHT_AI if use_ai_opcode else _SET_LIGHT_SHOW
                light_id = 0
                target_params = p["params"]
                value = (
                    _tlv_long(0xA3, target_cloud_id.to_bytes(2, "little"))
                    + _tlv_long(0xA4, target_params.encode())
                    + _tlv(0xA8, b"\x64")
                    + _tlv(0xA9, bytes(5))
                    + _tlv(0xAA, b"\x00")
                    + _tlv(0xAE, b"\x00")
                    + _tlv(0xB0, b"\x00")
                )
                payload = _command_payload(self._user_id, value)
        elif target_params is not None:
            # Custom JSON animation
            if device.model == _T8L40_MODEL:
                opcode = _SET_ANIMATION
                value = _build_t8l40_animation_payload(0, target_params, speed)
                payload = _command_payload(self._user_id, value)
            else:
                opcode = _SET_LIGHT_AI if use_ai_opcode else _SET_LIGHT_SHOW
                light_id = 0
                value = (
                    _tlv_long(0xA4, target_params.encode())
                    + _tlv(0xA8, b"\x64")
                    + _tlv(0xA9, bytes(5))
                    + _tlv(0xAA, b"\x00")
                    + _tlv(0xAE, b"\x00")
                    + _tlv(0xB0, b"\x00")
                )
                payload = _command_payload(self._user_id, value)
        elif colors is not None:
            opcode = _SET_EFFECT
            if not device.lamp_count:
                 raise EufyLifeCloudError("Waiting for the light's lamp count")
            if len(colors) != device.lamp_count:
                raise EufyLifeCloudError(f"Effect requires exactly {device.lamp_count} colors")
            light_id = 20006
            target_colors = colors
            value = _effect_payload(
                light_id, target_colors, target_direction, target_speed, None, 7
            )
            payload = _command_payload(self._user_id, value)
        else:
            # Single color for all segments
            if rgb_color is None and rgbww_color is None:
                 raise EufyLifeCloudError("Provide a color, effect, or color list")
            if not device.lamp_count:
                raise EufyLifeCloudError("Waiting for the light's lamp count")
            opcode = _SET_EFFECT
            light_id = 20006
            color = rgbww_color if rgbww_color is not None else rgb_color
            try:
                target_colors = [tuple(color)] * device.lamp_count
                value = _effect_payload(
                    light_id, target_colors, target_direction, target_speed, None, 7
                )
                payload = _command_payload(self._user_id, value)
            except (TypeError, ValueError) as err:
                raise EufyLifeCloudError("Invalid color or lamp count") from err

        async with self._effect_locks[serial]:
            future = self._loop.create_future()
            self._effect_replies[serial] = future
            try:
                self._publish(
                    serial, opcode, payload, version=1 if use_version_1 else 0
                )
                await asyncio.wait_for(future, timeout=_EFFECT_RESPONSE_TIMEOUT)
            except asyncio.TimeoutError as err:
                raise EufyLifeCloudError(
                    "Light did not acknowledge the color/preset"
                ) from err
            finally:
                self._effect_replies.pop(serial, None)

            # ponytail: ACK-backed selection, not palette readback
            device.light_id = light_id if light_id else target_cloud_id
            device.rgb_color = tuple(rgb_color) if rgb_color is not None else None
            device.rgbww_color = tuple(rgbww_color) if rgbww_color is not None else None
            device.effect = effect
            device.speed = target_speed
            device.direction = target_direction
            device.colors = target_colors
            if refresh:
                self.request_settings(serial)
            self._notify(serial)

    async def async_set_scene(
        self,
        serial: str,
        scene_id: int,
        refresh: bool = True,
    ) -> None:
        """Set a specific cloud scene by ID (Opcode 0202)."""
        device = self.devices[serial]

        # For T8L40, ensure a handshake has occurred for this connection
        if device.model == _T8L40_MODEL:
            await self.async_handshake(serial)
            await asyncio.sleep(0.5)

        value = _tlv(0xA3, scene_id.to_bytes(4, "little"))
        payload = _command_payload(self._user_id, value)

        async with self._effect_locks[serial]:
            future = self._loop.create_future()
            self._effect_replies[serial] = future
            try:
                self._publish(
                    serial, _SET_SCENE, payload
                )
                await asyncio.wait_for(future, timeout=_EFFECT_RESPONSE_TIMEOUT)
            except asyncio.TimeoutError as err:
                raise EufyLifeCloudError(
                    "Light did not acknowledge the scene change"
                ) from err
            finally:
                self._effect_replies.pop(serial, None)

            if refresh:
                self.request_settings(serial)
            self._notify(serial)

    def set_power(
        self, serial: str, is_on: bool, brightness: int | None = None
    ) -> None:
        """Publish power; only device settings/reports change the entity state."""
        value = _tlv(0xA3, bytes((int(is_on),)))
        if brightness is not None:
            if not 0 <= brightness <= 100:
                raise EufyLifeCloudError("Brightness must be between 0 and 100 percent")
            value += _tlv(0xA4, bytes((brightness,)))
        self._publish(
            serial,
            _SET_POWER,
            _command_payload(self._user_id, value),
        )

    def _publish(self, serial: str, opcode: tuple[int, int], payload: bytes, version: int = 0) -> None:
        if not self.connected or self._mqtt is None:
            raise EufyLifeCloudError("Eufy Life MQTT is not connected")
        device = self.devices[serial]

        self._msg_seq = (self._msg_seq + 1) % 1000
        timestamp = int(time.time())

        command = {
            "head": {
                "version": "1.0.0.1",
                "client_id": f"android-eufy_life-{self._user_id}",
                "sess_id": "1",
                "msg_seq": self._msg_seq,
                "cmd": 17,
                "cmd_status": 1,
                "sign_code": 0,
                "seed": "",
                "timestamp": timestamp,
            },
            "payload": json.dumps(
                {
                    "account_id": device.account_id,
                    "device_sn": serial,
                    "data": base64.b64encode(_frame(opcode, payload, version)).decode(),
                },
                separators=(",", ":"),
            ),
        }
        result = self._mqtt.publish(
            f"cmd/eufy_life/{device.model}/{serial}/req",
            json.dumps(command, separators=(",", ":")),
            qos=1,
        )
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            raise EufyLifeCloudError(f"Eufy Life MQTT publish failed: {result.rc}")

    def add_listener(self, serial: str, listener: Callable[[], None]) -> None:
        self._listeners[serial].add(listener)

    def remove_listener(self, serial: str, listener: Callable[[], None]) -> None:
        self._listeners[serial].discard(listener)

    def _notify(self, serial: str | None = None) -> None:
        listeners = (
            self._listeners.get(serial, ())
            if serial is not None
            else (listener for group in self._listeners.values() for listener in group)
        )
        for listener in tuple(listeners):
            self._loop.call_soon_threadsafe(listener)

    async def async_close(self) -> None:
        """Stop the MQTT client and remove its temporary certificate files."""
        self.connected = False
        if self._mqtt is not None:
            self._mqtt.disconnect()
            await asyncio.to_thread(self._mqtt.loop_stop)
            self._mqtt = None
        if self._mqtt_files is not None:
            self._mqtt_files.cleanup()
            self._mqtt_files = None


def _required_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise EufyLifeCloudError(f"MQTT response is missing {key}")
    return value


def _self_check() -> None:
    from unittest.mock import AsyncMock, MagicMock, patch

    payload = _tlv(0xA1, b"\x01") + _tlv(0xA2, b"owner")
    frame = _frame(_GET_SETTINGS_RESPONSE, payload)
    assert frame.hex() == "ff0914000300020a00a10101a2056f776e65728e"
    assert len(frame) == int.from_bytes(frame[2:4], "little")
    assert _parse_frame(frame) == (_GET_SETTINGS_RESPONSE, payload)
    assert _parse_tlvs(payload) == {0xA1: b"\x01", 0xA2: b"owner"}
    report = bytearray(_frame(_REPORT_DEVICE_INFO, _tlv(0xA1, b"\x01")))
    report[5] = 1  # Observed unsolicited report header: 03 01 02.
    report[-1] = _xor(report[:-1])
    assert _parse_frame(report) == (_REPORT_DEVICE_INFO, _tlv(0xA1, b"\x01"))
    key = bytes.fromhex(_LIGHT_PRESET_KEY)
    assert _aes_decrypt(_aes_encrypt("eufy", key), key) == "eufy"
    crypto = _LightCrypto()
    crypto._key = key
    crypto._security_key = key.hex()
    plaintext = '{"code":0,"data":{"devices":[]}}'
    encrypted = _aes_encrypt(plaintext, key)
    response = {
        "code": 0,
        "data": encrypted,
        "signature": _signature(f"123+nonce+{encrypted}", key.hex()),
    }
    assert crypto.decrypt(json.dumps(response), "123", "nonce") == plaintext
    response["signature"] = "0" * 64
    try:
        crypto.decrypt(json.dumps(response), "123", "nonce")
    except EufyLifeCloudError as err:
        assert str(err) == "Invalid encrypted light response signature"
    else:
        raise AssertionError("Tampered light response was accepted")

    async def check_empty_inventory() -> None:
        async with aiohttp.ClientSession() as session:
            cloud = EufyLifeLightCloud(session, "", "", "", "", "DE", "en", "UTC")
            response = MagicMock()
            response.json = AsyncMock(
                return_value={
                    "code": 0,
                    "data": {"domain": "aiot-light-api-eu.eufylife.com"},
                }
            )
            with (
                patch.object(session, "post") as post,
                patch.object(
                    cloud._crypto, "async_exchange", new=AsyncMock()
                ) as exchange,
                patch.object(
                    cloud, "_async_post", new=AsyncMock(return_value={"devices": None})
                ),
            ):
                post.return_value.__aenter__.return_value = response
                await cloud.async_start()
                assert cloud.devices == {}
                exchange.assert_awaited_once_with(
                    session, "https://aiot-light-api-eu.eufylife.com"
                )
            inventory = {
                "devices": [
                    {
                        "device": {
                            "device_sn": "shared",
                            "device_model": "T8L30",
                            "member": {"member_type": 1, "admin_user_id": "owner"},
                        }
                    }
                ]
            }
            with (
                patch.object(session, "post") as post,
                patch.object(cloud._crypto, "async_exchange", new=AsyncMock()),
                patch.object(
                    cloud,
                    "_async_post",
                    new=AsyncMock(side_effect=[inventory, {"list": []}, {}]),
                ),
                patch.object(cloud, "_start_mqtt"),
            ):
                post.return_value.__aenter__.return_value = response
                await cloud.async_start()
            cloud._user_id = "member"
            cloud.connected = True
            cloud._mqtt = MagicMock()
            cloud._mqtt.publish.return_value.rc = mqtt.MQTT_ERR_SUCCESS
            cloud.request_settings("shared")
            sent = json.loads(cloud._mqtt.publish.call_args.args[1])
            inner = json.loads(sent["payload"])
            assert inner["account_id"] == "owner"
            _, body = _parse_frame(base64.b64decode(inner["data"]))
            assert _parse_tlvs(body)[0xA2] == b"member"
            cloud._handle_frame("shared", _GET_SETTINGS_RESPONSE, b"\x00\xa1\x01\x01")
            assert cloud.devices["shared"].is_on is True
            cloud._handle_frame("shared", _GET_SETTINGS_RESPONSE, b"\x01\xa1\x01\x00")
            assert cloud.devices["shared"].is_on is True
            cloud.set_power("shared", False)
            cloud._handle_frame("shared", _SET_POWER_RESPONSE, b"\x00")
            assert cloud.devices["shared"].is_on is True  # ACK is not a state report.
            cloud._handle_frame("shared", _REPORT_DEVICE_INFO, b"\xa1\x01\x00")
            assert cloud.devices["shared"].is_on is False
            from .light import EufyLifeLight
            from homeassistant.components.light import ColorMode

            light = EufyLifeLight(cloud, cloud.devices["shared"])
            assert light.supported_color_modes == {ColorMode.RGBWW}
            cloud._handle_frame("shared", _GET_SETTINGS_RESPONSE, b"\x00\xa2\x01\x32")
            assert light.brightness == 128
            await light.async_turn_on(brightness=255)
            sent = json.loads(cloud._mqtt.publish.call_args.args[1])
            inner = json.loads(sent["payload"])
            op, body = _parse_frame(base64.b64decode(inner["data"]))
            assert op == _SET_POWER
            assert _parse_tlvs(body)[0xA3] == b"\x01"
            assert _parse_tlvs(body)[0xA4] == b"\x64"
            assert light.brightness == 128  # Wait for device state, not PUBACK.

            # Captured DIY command: one RGBWC palette entry, four lamp indices.
            values = _parse_tlvs(
                _effect_payload(20006, [(255, 0, 0)] * 4, 0, 1, None, 7)
            )
            assert values[0xA3] == bytes.fromhex("264e")
            assert values[0xA6] == bytes.fromhex("01ff00000000")
            assert values[0xA7] == bytes.fromhex("0400010203")
            assert values[0xA8] == b"\x64"
            assert values[0xA9] == bytes(5)
            assert values[0xB0] == b"\x07"
            assert 0xAC not in values
            animation_payload = _build_t8l40_animation_payload(10854, "{}", 54)
            assert animation_payload.startswith(
                bytes.fromhex("a304662a0000a40136a50102a60103a80100a935")
            )
            assert tuple(_frame(_SET_ANIMATION, animation_payload, version=0)[7:9]) == _SET_ANIMATION
            preset = {
                "name": "White",
                "dynamic": "44",
                "dynamic_direct": "0",
                "rgb_hex": "FFDCB3",
                "speed": 1,
                "light_id": 30024,
            }
            catalog = {
                "list": [
                    {
                        "scene_info": [
                            {
                                "light": [
                                    preset,
                                    dict(
                                        preset,
                                        name="New format",
                                        params_version=1,
                                        params="{}",
                                    ),
                                    dict(preset, name="Bad palette", rgb_hex="xyz"),
                                ]
                            }
                        ]
                    }
                ]
            }
            effects = _parse_effects(catalog)
            assert list(effects) == ["White", "New format"]
            assert effects["White"]["light_id"] == 30024
            device = cloud.devices["shared"]
            cloud._handle_frame(
                "shared", _GET_SETTINGS_RESPONSE, bytes.fromhex("00a3020400a402264e")
            )
            assert device.lamp_count == 4 and device.light_id == 20006
            device.effects = effects
            assert light.effect_list == ["White", "New format"]
            task = asyncio.create_task(light.async_turn_on(rgb_color=(255, 0, 0)))
            await asyncio.sleep(0)
            assert light.rgb_color is None
            cloud._handle_frame("shared", (10, 6), bytes.fromhex("00a10100"))
            await task
            assert light.rgb_color == (255, 0, 0)
            task = asyncio.create_task(light.async_turn_on(effect="White"))
            await asyncio.sleep(0)
            cloud._handle_frame("shared", (10, 6), bytes.fromhex("00a10101"))
            from homeassistant.exceptions import HomeAssistantError

            try:
                await task
            except HomeAssistantError:
                pass
            else:
                raise AssertionError("Rejected preset was accepted")
            assert light.effect is None and light.rgb_color == (255, 0, 0)
            task = asyncio.create_task(light.async_turn_on(effect="White"))
            await asyncio.sleep(0)
            sent = json.loads(cloud._mqtt.publish.call_args.args[1])
            _, body = _parse_frame(
                base64.b64decode(json.loads(sent["payload"])["data"])
            )
            assert _parse_tlvs(body)[0xA3] == b"\x2c\x00"
            assert _parse_tlvs(body)[0xAC] == (30024).to_bytes(4, "little")
            cloud._handle_frame("shared", (10, 6), bytes.fromhex("00a10100"))
            await task
            assert light.effect == "White" and light.rgb_color is None
            cloud._handle_frame(
                "shared", _REPORT_DEVICE_INFO, bytes.fromhex("a4042d000000")
            )
            assert (
                light.rgb_color is None
            )  # External animation change invalidates cache.

    asyncio.run(check_empty_inventory())


if __name__ == "__main__":
    # Use the same module/class identity as light.py when invoked with python -m.
    from . import cloud

    cloud._self_check()
