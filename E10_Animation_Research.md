### Technical Document: E10 Floor Lamp (T8L40) Animation Protocol Research

#### Objective
Enable high-fidelity, JSON-based animations (e.g., Fireworks) for the Eufy E10 Floor Lamp (model T8L40) within the `eufylife_api` HACS integration.

---

#### 1. The Rosetta Stone: T8L40 Protocol Map

| Component | V0 (Standard Control) | V1 (Reports / Modern) |
| :--- | :--- | :--- |
| **Byte 5 (Version)** | `00` | `01` |
| **Envelope** | **Required** (A1: Time, A2: User) | Usually None (Raw Tags) |
| **Primary Opcode** | `0201`, `0202`, `0206` | `0204`, `1000` |
| **A1 Label** | Timestamp (4b) or Power (1b) | Power (1b) |
| **A2 Label** | User ID (40b) or Brightness (1b) | Brightness (1b) |
| **A3 Label** | Payload (4b) or Segments (1b) | Segments (**2b** in reports) |
| **A4 Label** | N/A (in V0 reqs) | Internal ID (**2b** in reports) |
| **A7 Label** | N/A | Mode (1b for Effect, 16b Static) |
| **AB Label** | Animation JSON (V0-AB) | Animation JSON (V1-AB) |

---

#### 2. Discovered Opcodes (Commands)

| Opcode | Name | Type | Key Discovery |
| :--- | :--- | :--- | :--- |
| **`0200`** | Session Handshake | V0 | Required to initialize session / unlock communications. |
| **`0201`** | Set Power | V0 | Basic ON/OFF toggle. |
| **`0202`** | Set Scene | V0 | Cloud scene shortcut; silent ACK on T8L40 but does not render animations. |
| **`0204`** | State Report | V1 | Unsolicited state report broadcast by lamp when effects/power change. |
| **`0206`** | Set Effect | V0 | Classic dynamic effects & static segment DIY colors (e.g. 20006). |
| **`020D`** | **Set Animation** | **V0** | **The Authentic App Method.** Binary-encoded multi-layer animation structs. |
| **`0A0D`** | Animation ACK | V0 | Immediate status `00` acknowledgment for `020D`. |
| **`1000`** | Heartbeat | V1 | Periodic status report verifying device connectivity. |

---

#### 3. Label (Tag) Deep Dive for T8L40

- **`A1` (4 bytes)**: Unix Timestamp (Little Endian). Part of the command envelope.
- **`A1` (1 byte)**: Power Status in raw frames.
- **`A2` (40 bytes)**: User ID Hex String. Part of the command envelope.
- **`A2` (1 byte)**: Brightness percentage (0-100).
- **`A3` (4 bytes)**: Scene ID or Session Mask.
- **`A3` (2 bytes)**: Lamp Count (`0c 00`) in reports.
- **`A4` (2 bytes)**: Internal Effect ID (e.g., `66 2a` for Fireworks).
- **`A6` (4 bytes)**: Official Cloud ID.
- **`A7` (1 byte)**: Mode. `02` for animations, `10 00...` for static.
- **`A9` (12 bytes)**: Auxiliary IDs block.
- **`AB` (Long TLV)**: The multi-layer JSON defining the animation.

---

#### 4. The "Silent State" Mismatch
We have successfully reached a state where the lamp's heartbeat reports it is in Fireworks mode (`A4: 10854, A7: 02`), yet the physical light remains on the previous static color (Red). This confirms that the **Rendering Engine** requires a specific trigger that is independent of the **State Register**.

#### 5. Verification History (Key Batches)
- **Batch 27**: Set Red via `0206 + V0 + Envelope`. **Full Success.**
- **Batch 32**: Set Fireworks State via `0204 + V1 + No Envelope`. **Silent Success** (Internal state only).
- **Batch 35**: Set Fireworks via `0206 + V0 + Envelope`. **Failed.** (JSON too large for V0 buffer?).
- **Batch 36**: Set Fireworks via `0202 + V0 + Envelope`. **Silent Success** (Status 00 but state reverted).
- **Interactive menu regression**: Selecting preset `26` with blank speed and direction
  took the `scene_id=10095` shortcut in `async_set_effect`. The lamp acknowledged
  `0202`, but the existing render continued; this is not proof that the animation ran.
- **App capture**: The visible-animation command was `0206`, frame version `01`,
  with `A3=0c`, `A4=10854` as a 2-byte internal ID, `A6=10854` as a 4-byte cloud
  ID, `A7=02`, long `AB` JSON, and trailing `A8`, `A9` (12 bytes), `AE`, and `B0`.

#### 6. Current Corrected Activation Path
For built-in T8L40 entries that contain `params`, option `4` must use the
captured `0206/v1` effect command even when speed and direction are left blank.
The `0202` scene shortcut remains useful as an advanced diagnostic, but its
acknowledgement only confirms command acceptance, not physical rendering.

---

#### 7. Final Summary of Findings
- **V0 vs V1 Partition**: Simple commands (Power/Scene/Handshake) use Version 0 with an Envelope. Modern renders (Animations) use Version 1 without an Envelope.
- **Handshake Unlock**: The T8L40 requires a Session Handshake (`0200`) before it will physically trigger any Version 1 rendering commands.
- **Tag Precision**: For the captured effect request, `A3` is the one-byte lamp
  count, `A4` is the two-byte internal effect ID, and `A6` is the four-byte cloud ID.

#### 8. Final Implementation Sequence
1.  **Handshake**: `0200` + V0 + Envelope + Mask `ff010000`.
2.  **Activate/render**: `0206` + V1 + Envelope + `A3/A4/A6/A7/AB/A8/A9/AE/B0`.
3.  **Verify**: Check the physical lamp; a status `00` or heartbeat ID alone is
    insufficient because the silent-state failure is possible.

#### 9. Latest Real-Device Validation (2026-09-16)
- Baseline was collected from serial `T8L4081024334CBC`: the lamp was `ON` at
  `30%`, with `A4=1f00` and `A7=00`; it was not reporting Fireworks mode.
- The named command path was run with `scripts/set_env.sh` and
  `--animation "Guy Fawkes Night"`; the catalog contained the expected preset.
- The command performed the `0200` handshake successfully and emitted the
  captured `0206`/version-1 animation request.
- No `opcode=(10, 6)` acknowledgement was received. The client timed out in
  `async_set_effect` and raised `Light did not acknowledge the color/preset`.
- A successful handshake and transmitted `0206` frame therefore remain
  insufficient evidence; the post-command internal state and visual output
  must both be checked. This run did not confirm Fireworks rendering.
- Visual confirmation after the run: no change; the lamp continued its
  existing rainbow animation rather than switching to Guy Fawkes Night.

#### 10. Protocol Version & Scenario Comparison Matrix (2026-09-16)

| Scenario / Goal | Protocol Version & Opcode | Framing & Payload Details | Observed Lamp Behavior | Status / Verdict |
| :--- | :--- | :--- | :--- | :--- |
| **JSON Animation Payload** | **V1 (`01`)**<br>`0206` (Set Effect) | Frame byte 5 = `01`. `_t8l40_animation_payload` with envelope tags `A1/A2/A3/A4/A6/A7`, long `AB` JSON, and trailing `A8/A9/AE/B0`. Handshake `0200` sent beforehand. | **Timeout**. Lamp never returned `(10, 6)` acknowledgement. Physical render remained unchanged. | **FAILED** (Device firmware does not parse raw JSON over `0206`). |
| **Cloud Scene ID Trigger** | **V0 (`00`)**<br>`0202` (Set Scene) | Frame byte 5 = `00`. Envelope (`A1` timestamp, `A2` user ID) + tag `A3` 4-byte scene ID (e.g., `10095`). | **Silent ACK**. Lamp returned status `00`, but physical rendering engine did not adopt or switch animation. | **FAILED** (State accepted, but rendering did not change). |
| **Classic Dynamic Effect Trigger** | **V0 (`00`)**<br>`0206` (Set Effect) | Frame byte 5 = `00`. Envelope (`A1` timestamp, `A2` user ID) + `_effect_payload`: tag `A3` dynamic ID (e.g. `20006`), tag `A6` palette bytes, tag `A7` lamp indices, tag `A8` speed, tag `A9` direction, tag `AC` cloud ID. | **Static / Non-Rendering ACK**. Lamp responded with status `00` on opcode `(10, 6)` and state showed `20006`, but `20006` is static DIY segment color mode. Physical animation did not play. | **FAILED** (Only sets static segment colors; does not trigger animation engine). |
| **Binary Layer Animation Trigger** | **V0 (`00`)**<br>`020D` (Set Animation) | Frame byte 5 = `00`. Envelope (`A1` timestamp, `A2` user ID) + tag `A3` effect ID (4B LE), tag `A4` speed (1B), tag `A5` layer count (1B), tag `A6` execution mode (1B), tag `A8` (1B 0x00), tags `A9`, `AA` binary-packed layer structs. | **Full Success**. Lamp immediately acknowledges with status `00` on opcode `(10, 13)` (`0A0D`), emits state report `(2, 4)` (`0204`), and immediately renders the physical multi-layer animation on the LEDs. Validated bidirectionally between *Guy Fawkes Night* and *Celebrating*. | **VERIFIED WORKING** (Official Eufy Life App protocol). |

---

#### 11. Complete Deep-Dive: The Authentic `020D` Animation Engine

##### Discovery Methodology
A live MQTT packet sniffer was deployed against the active AWS IoT MQTT broker, subscribing to `cmd/eufy_life/#` and `synq/eufy_life/#`. The user activated built-in animations (*Guy Fawkes Night* ID `10854`, *Celebrating* ID `10599`, *Party* ID `10589`) from the official Eufy Life mobile application on lamp `T8L4081024334CBC`.

##### Command Frame Anatomy
- **Command Opcode**: `(0x02, 0x0D)` (`020D`)
- **Response Opcode**: `(0x0A, 0x0D)` (`0A0D`), payload: `00 a1 01 00` (Status `00`)
- **Protocol Version**: Version 0 (`0x00`), with standard envelope:
  * `Tag 0xA1` (4 bytes, uint32 LE): Current Unix timestamp
  * `Tag 0xA2` (40 bytes, ASCII): 40-character User ID hex string
- **Animation Body Tags**:
  * `Tag 0xA3` (4 bytes, uint32 LE): Cloud Light/Effect ID (`10854` = `66 2a 00 00`, `10599` = `67 29 00 00`)
  * `Tag 0xA4` (1 byte, uint8): Effect speed (from JSON `light_effect_speed`, e.g. `54` = `0x36`)
  * `Tag 0xA5` (1 byte, uint8): Number of layers (from JSON `len(layer)`, e.g. `2` = `0x02`)
  * `Tag 0xA6` (1 byte, uint8): Layer execution mode (from JSON `layer_execution_mode`, e.g. `3` or `0`)
  * `Tag 0xA8` (1 byte, uint8): Mode flag (`0x00`)
  * `Tag 0xA9`: Layer 0 packed binary data
  * `Tag 0xAA`: Layer 1 packed binary data (if layer count $\ge 2$)
  * `Tag 0xAB`: Layer 2 packed binary data (if layer count $\ge 3$)

##### Layer Binary Struct Specification
The Eufy application compiles the catalog JSON animation layers into packed C structs before transmission. Each layer comprises a 10-byte common header, a variable-length palette block, and type-specific execution parameters:

1. **Common Header (10 bytes)**:
   * Offset `0x00` (1B): `layer_priority` (uint8)
   * Offset `0x01` (1B): `layer_speed` (uint8)
   * Offset `0x02` (1B): `layer_range[1]` (uint8, upper segment limit, e.g. `100` = `0x64`)
   * Offset `0x03` (1B): `layer_range[0]` (uint8, lower segment limit, e.g. `0` = `0x00` or `75` = `0x4B`)
   * Offset `0x04` (1B): `interval_type` (uint8, default `1`)
   * Offset `0x05` (1B): `interval_value` (uint8, default `1`)
   * Offset `0x06-0x07` (2B, Big Endian uint16): `layer_execution_parameter` (e.g. `130` = `0x0082`, `128` = `0x0080`)
   * Offset `0x08` (1B): `light_effect_post_cycle_status` (uint8)
   * Offset `0x09` (1B): `current_layer_type` (`0` = Flow/Insert, `1` = Cycle/Gradient, `2` = Twinkle/Blink)

2. **Colors Block ($1 + 5N$ bytes)**:
   * Offset `0x0A` (1B): Color count $N$ (e.g. `5` = `0x05`)
   * Followed by $N$ 5-byte color entries: Eufy 5-channel PWM drive levels `[R, G, B, W, C]` calibrated for the T8L40 LED strip hardware.

3. **Type-Specific Parameters**:
   * **Type 0 (`current_layer_type == 0`, 17 trailing bytes)**:
     `color_fill_mode` (1B), `color_pick_mode` (1B), `flow_direction` (1B), `direction_change_mode` (1B), `insert_block_mode` (1B), `insert_block_range` (2B BE uint16), `insert_black_block_mode` (1B), `insert_black_block_range` (2B BE uint16), `insert_position_mode` (1B), `brightness_variation_type` (1B), `brightness_range[1]` (1B), `brightness_range[0]` (1B), `light_effect_cycle_method` (1B), `execution_parameter` (1B).
   * **Type 1 (`current_layer_type == 1`, 10 trailing bytes)**:
     `brightness_value` (1B), `display_mode` (1B), `color_quantity_range` (1B), `transition_mode` (1B), `color_switch_mode` (1B), `color_pick_sequence` (1B), `reserved` (2B: `00 00`), `light_effect_cycle_method` (1B), `execution_parameter` (1B).
   * **Type 2 (`current_layer_type == 2`, 12 trailing bytes)**:
     `brightness_variation_type` (1B), `brightness_range[1]` (1B), `brightness_range[0]` (1B), `blink_cycle_count` (1B), `blink_position_mode` (1B), `blink_interval[1]` (1B), `blink_interval[0]` (1B), `blink_quantity` (1B), `blink_asynchrony` (1B), `blink_color_switch_mode` (1B), `light_effect_cycle_method` (1B), `execution_parameter` (1B).

##### Lamp State Broadcast (`0204`)
Upon receiving and starting a `020D` animation, the physical lamp emits an unsolicited state broadcast on opcode `(0x02, 0x04)` (Version 1, len=51):
* `Tag 0xA1` (1B): Power status (`01` = ON)
* `Tag 0xA2` (1B): Brightness (`1E` = 30%)
* `Tag 0xA3` (1B): Segment count (`0C` = 12)
* `Tag 0xA4` (4B LE uint32): Active effect ID (e.g. `10854` = `66 2a 00 00`)
* `Tag 0xA5` (1B): Sub-status (`00`)
* `Tag 0xA6` (4B LE uint32): Cloud preset ID (`10854`)
* `Tag 0xA7` (1B): Operational Mode (`02` = Dynamic Animation Mode)
* `Tag 0xA9` (12B): Auxiliary state transition record

##### Ground Truth Captures
- **Guy Fawkes Night (`10854`)**:
  ```hex
  ff09b300030002020da104086faa6aa22834366337643131633561386538663361343438633236666266363436353465366464303638353135a304662a0000a40136a50102a60103a80100a9350164640001010082010005ff01130500ff00146200ff01280c008701ff001103ff740017000000000000000300000900046410020caa2e0014644b0101000103010503ff7400178701ff0011ff01280c0002ff011701ff0113050061010202000000010002c6
  ```
- **Celebrating (`10599`)**:
  ```hex
  ff09ae00030002020da1048b6eaa6aa22834366337643131633561386538663361343438633236666266363436353465366464303638353135a30467290000a4011ea50102a60100a80100a92e0014640001010080000105ff010b0600ff000b0d00ffa1000b0001ff85000e04ffc3003864010502000000000001aa300019640001010080000205ff010b0600ff000b0d00ffa1000b0001ff85000e04ffc3003803640901010502ff0102000142
  ```
- **Party (`10589`)**:
  ```hex
  ff097c00030002020da104946eaa6aa22834366337643131633561386538663361343438633236666266363436353465366464303638353135a3045d290000a4011ca50101a60100a80100a92e00246400010100800001059600ff000bfc01470800ff56000000ff01404e0013ff059d3464010502000000000100b9
  ```
