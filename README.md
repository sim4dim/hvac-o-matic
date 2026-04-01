# Keenect — Smart HVAC Zone Control

<!-- TODO: Add badges (HA version, ESPHome version, license) -->

Add per-room temperature control to a standard single-zone forced-air furnace using Keen Smart Vents, an ESP32 furnace controller, and Home Assistant automation logic. No HVAC contractor required.

<!-- TODO: Add screenshots -->

---

## Table of Contents

1. [Overview](#overview)
2. [System Architecture](#system-architecture)
3. [Hardware Requirements](#hardware-requirements)
4. [Software Requirements](#software-requirements)
5. [How It Works](#how-it-works)
6. [Safety Design](#safety-design)
7. [Installation](#installation)
8. [Configuration](#configuration)
9. [Project Structure](#project-structure)
10. [Legacy / History](#legacy--history)
11. [Contributing](#contributing)
12. [License](#license)

---

## Overview

Most homes have a single-zone forced-air HVAC system: one thermostat controls the whole house. Rooms with more sun exposure, different insulation, or different occupancy patterns are always too hot or too cold.

Keenect solves this by adding per-room temperature setpoints on top of your existing system without replacing the furnace or ductwork:

- **Keen Smart Vents** (Zigbee motorized dampers) control airflow into each room proportionally based on how far that room is from its setpoint.
- **An ESP32-S3 controller** with a relay board takes over from your wall thermostat to run the furnace only when one or more zones demand it.
- **Home Assistant** runs the control loop, calculates demand across all zones, and coordinates everything.
- A **hardware bypass** ensures your original thermostat retakes control if anything fails.

The system currently controls four zones (master bedroom, two kids' rooms, first floor) with additional passive temperature monitoring in other rooms.

---

## System Architecture

```
┌─────────────┐     Zigbee      ┌──────────────┐
│ Keen Vents  │◄───────────────►│ Zigbee Hub   │
│ (per room)  │                 │ (Hubitat C-8)│
└─────────────┘                 └──────┬───────┘
                                       │ Integration
┌─────────────┐     ESPHome API  ┌─────┴────────┐     MQTT        ┌──────────────┐
│ 1st Floor   │◄────────────────►│              │───heartbeat────►│ ESP32-S3     │
│ Servo (ESP32)│                 │ Home         │                 │ HVAC         │
└─────────────┘                 │ Assistant    │                 │ Controller   │
                                │              │                 │              │
┌─────────────┐                 │ Pyscript:    │                 │ 8ch Relay    │
│ Thermostats │◄───────────────►│ keenect_ha   │                 │ Board        │
│ (HA climate)│  climate_tmpl   │              │                 └──────┬───────┘
└─────────────┘                 └──────────────┘                        │ 24VAC
                                                                 ┌──────┴───────┐
                                                                 │   Furnace    │
                                                                 │ Heat/Cool/Fan│
                                                                 └──────────────┘
```

**Control flow:**

1. Each zone has an HA climate entity with its own temperature setpoint.
2. Pyscript (`keenect_ha.py`) evaluates all zones every 15 seconds.
3. If any zone demands heat or cool (temp deviates from setpoint beyond hysteresis), the script commands the ESP32 to energize the appropriate relay.
4. Vent positions are calculated proportionally — zones that need more conditioning get more airflow, satisfied zones close their vents.
5. The ESP32 publishes MQTT heartbeats to confirm HA is alive. If heartbeats stop, the ESP32 falls back to bypass mode and hands control back to the wall thermostat.

**Two ESP32 devices:**

- `hvac-controller` (ESP32-S3-WROOM-1): Main furnace relay controller with BME280/BME680 duct sensors and SSD1306 OLED display.
- `hvac-1st-floor` (Heltec WiFi Kit 32 V3): Controls a servo-driven register for the first-floor zone, with its own OLED display.

---

## Hardware Requirements

| Component | Purpose | Notes |
|-----------|---------|-------|
| ESP32-S3-WROOM-1-N16R8 | Main furnace controller | 16MB flash, 8MB PSRAM; ~$7/unit in 3-packs (AYWHP) |
| 8-channel 5V relay module | Furnace relay switching | Kootek or equivalent; active-low |
| 10K resistors (8x) | Pull-ups on relay GPIOs | Prevent relay chatter during ESP32 boot |
| BME280 (I2C) | Supply duct temperature/humidity | Monitors air coming out of furnace |
| BME680 (I2C) | Return duct temperature/humidity/VOC | Monitors air returning to furnace |
| SSD1306 128x64 OLED | Status display on controller | I2C, shared bus with sensors |
| Keen Smart Vents | Per-room airflow control | Zigbee; 1–2 per zone |
| Heltec WiFi Kit 32 V3 | 1st floor servo controller | Includes built-in OLED |
| Standard servo motor | Register damper control | Controls 1st floor supply register |
| 24VAC to 5V DC converter | Power from furnace transformer | Tap R/C wires from furnace |
| Zigbee coordinator | Keen vent radio hub | Hubitat C-8 used here; any coordinator works |
| Pearl thermostat | Bypass / fallback thermostat | Stays wired in; takes over if ESP32 fails |

**Wiring note:** The relay board requires 10K pull-up resistors from each relay GPIO to 3.3V. Without them, GPIOs float low during boot and the relays fire briefly — causing spurious furnace activation on every reboot.

---

## Software Requirements

### Home Assistant
- Home Assistant (any recent release)
- **Pyscript** integration (`allow_all_imports: true`)
- **HACS** with:
  - [`climate_template`](https://github.com/jcwillox/hass-template-climate) — provides per-zone climate entities with custom temperature sensors
  - [`fold-entity-row`](https://github.com/thomasloven/lovelace-fold-entity-row) — collapsible settings rows in the dashboard

### ESPHome
- ESPHome (any recent release)
- MQTT broker on the local network (no authentication required by default)

### Optional
- Hubitat Elevation hub (for Zigbee radio if not using a dedicated Zigbee coordinator)

---

## How It Works

### Zone evaluation

Every 15 seconds, pyscript evaluates each zone:

1. Read the zone's current temperature from its dedicated sensor.
2. Compare against the active setpoint (heat or cool depending on HVAC mode).
3. If the deviation exceeds the hysteresis threshold, the zone is "demanding."
4. Calculate a vent opening percentage proportional to how much conditioning is needed.

### HVAC demand aggregation

After evaluating all zones:

- If any zone is demanding heat, and the system is in heat mode: turn on heat.
- If no zones demand heat: turn off heat (after the vent closure delay).
- Same logic applies for cool mode.
- A global `input_select.hvac_mode` drives heat vs. cool across all zones simultaneously.

### Vent positioning

Keen Smart Vents are controlled via Zigbee as `light` entities — `light.turn_on(brightness_pct=N)` sets the vent to N% open. The first-floor zone uses a servo register exposed as a `number` entity via ESPHome.

Vent control modes:
- **Demanding zones**: open proportionally (min_vo to max_vo range)
- **Satisfied zones**: close to min_vo (never fully closed — prevents duct overpressure)
- **Fan circulation**: all zones open to `fan_vo`
- **Vent closure delay**: 120 seconds after heat turns off before vents close (furnace spin-down is ~110 seconds; closing early causes overpressure)

### Heartbeat watchdog

Pyscript publishes an MQTT heartbeat every 60 seconds to `keenect/heartbeat`. The ESP32 monitors this topic. If no heartbeat is received for 5 minutes, the ESP32 de-energizes the changeover relays, returning the Pearl thermostat to direct control of the furnace. This covers the failure mode where HA is running but pyscript has crashed.

---

## Safety Design

**This system controls a gas furnace. Incorrect wiring or software bugs can damage equipment, void warranties, or cause fires. Read this section carefully before installation.**

### Hardware bypass (fail-safe)

The relay board uses a changeover relay architecture:

- **Relays 5, 6, 7** are changeover relays (SPDT) wired between the furnace terminals (W/Y/G) and both the Pearl thermostat (NC) and the ESP32 control relays (NO).
- When the changeover relays are **de-energized** (NC position), the Pearl thermostat controls the furnace directly. This is the default state.
- When the changeover relays are **energized** (NO position), the ESP32 relays control the furnace. Pearl is powered but disconnected.
- If the ESP32 loses power or crashes, all relays de-energize. The furnace falls back to Pearl thermostat control. **No code required — this is a hardware property.**

### Bypass modes

| Condition | Behavior |
|-----------|---------|
| ESP32 boots normally | Stays in bypass until HA API connects |
| HA API connects | Exits bypass (unless physical toggle switch is set) |
| HA API disconnects | 60-minute bypass watchdog timer starts |
| Pyscript heartbeat lost (5 min) | Enters bypass immediately |
| Physical bypass switch pressed | Enters bypass regardless of software state |
| Manual bypass command from HA | Enters bypass |

### Short-cycle protection

- **Compressor**: minimum 5 minutes between cool-off and cool-on (refrigerant equalization)
- **Heat exchanger**: minimum 2 minutes between heat-off and heat-on

### Other safety features

- **Max-run timer**: If heat or cool runs continuously for 2 hours, the ESP32 forces bypass and publishes an alert to `hvac/errors`.
- **Supply temperature limit**: If the BME280 reads supply air above 160°F during heating, heat shuts off immediately (Lennox G61MPV heat exchanger limit).
- **Vent closure delay**: 120 seconds after furnace turns off before vents close. Prevents overpressure during furnace blower spin-down (~110 seconds).
- **MQTT error alerts**: All fault conditions publish to `hvac/errors`.

---

## Installation

### 1. Flash the ESP32 firmware

Copy `esphome/secrets.yaml.example` to `esphome/secrets.yaml` and fill in your Wi-Fi credentials, API key, and OTA password.

```bash
esphome run esphome/hvac-controller.yaml
esphome run esphome/hvac-1st-floor.yaml
```

### 2. Wire the relay board

Follow the wiring diagram in `esphome/hvac-controller.yaml` (lines 12–99). Key points:

- Add 10K pull-up resistors from each relay GPIO (4, 5, 6, 7, 15, 16, 17, 18) to 3.3V.
- Relays 5, 6, 7 are changeover relays — wire NC terminals to the Pearl thermostat outputs and NO terminals to the ESP32 control relay outputs (relays 1, 2, 3).
- Power the relay board from a 24VAC-to-5V converter tapped from the furnace R/C terminals.

### 3. Deploy HA configuration

Copy the package files to `/config/packages/` on your HA instance:

```bash
scp ha/packages/keenect.yaml     hassio@<HA_IP>:/config/packages/
scp ha/packages/thermostats.yaml hassio@<HA_IP>:/config/packages/
```

Deploy the pyscript file:

```bash
scp ha/pyscript/keenect_ha.py hassio@<HA_IP>:/config/pyscript/
```

Deploy the dashboard:

```bash
scp ha/dashboards/keenect.yaml hassio@<HA_IP>:/config/dashboards/
```

Restart Home Assistant to load the packages and pyscript.

### 4. Install HACS components

In HACS, install:
- `climate_template` by jcwillox
- `fold-entity-row` by thomasloven

### 5. Create the persisted state helper

The pyscript uses a long-lived `input_text` entity that must be created via the HA WebSocket API (not YAML, to survive config reloads):

In HA Settings > Helpers, create an Input Text named `keenect_persisted_state` with max length 255.

### 6. Enable Keenect

Keenect starts **disabled** by default. Once wiring is verified and all zones are reporting temperatures, enable it:

- In HA: toggle `input_boolean.keenect_enabled` to ON.
- Verify that the furnace responds to a zone demand (raise a setpoint above current room temp in heat mode).

> **Critical**: Always verify the furnace responds to demand after any configuration change. Silent failures can leave the house without heat.

---

## Configuration

### Zone settings

Zones are defined in `ha/pyscript/keenect_ha.py` in the `_HARDCODED_ZONES` dict. Each zone specifies:

```python
"ben": {
    "thermostat": "climate.ben_s_room",    # HA climate entity
    "temp_sensor": "sensor.gw1000_temp_ch7",  # raw temperature sensor
    "vents": ["light.keen_ben"],            # Keen vent entity (or servo number)
    "vent_type": "light",                   # "light" for Keen, "number" for servo
    "health_sensors": ["sensor.keen_ben_pressure"],
    "heat_min_vo": 15, "heat_max_vo": 100, # vent % range during heat
    "cool_min_vo": 15, "cool_max_vo": 100, # vent % range during cool
    "fan_vo": 30,                           # vent % during circulation-only
    "vent_control": "Aggressive",
},
```

For a servo-based zone (first floor), `vent_type` is `"number"` and the min/max values are servo angles in degrees (max 45°).

### Adjustable parameters (HA helpers)

| Helper | Default | Description |
|--------|---------|-------------|
| `input_number.keenect_hysteresis` | 1.0°F | Dead band around setpoint before demanding |
| `input_number.vent_closure_delay` | 120s | Delay before closing vents after furnace off |
| `input_number.cool_lockout_temp` | 65°F | Outdoor temp below which cooling is locked out |
| `input_select.hvac_mode` | heat | Global mode: heat / cool / fan / off |
| `input_boolean.keenect_enabled` | off | Master enable switch |

### Thermostat entities

Each zone uses a `climate_template` entity defined in `ha/packages/thermostats.yaml`. The template reads setpoints from `input_number` helpers and publishes temperature from the zone's dedicated sensor. This keeps the display thermostat UI working while pyscript handles all the logic underneath.

---

## Project Structure

```
hvac/
├── ha/
│   ├── pyscript/
│   │   └── keenect_ha.py          # Main HVAC zone control logic (v2.5.0)
│   ├── packages/
│   │   ├── keenect.yaml           # Input helpers and sensors
│   │   ├── keenect_zones.yaml     # Zone configuration
│   │   └── thermostats.yaml       # Climate entity definitions
│   └── dashboards/
│       └── keenect.yaml           # Lovelace dashboard
├── esphome/
│   ├── hvac-controller.yaml       # ESP32-S3 furnace controller
│   ├── hvac-1st-floor.yaml        # 1st floor servo register controller
│   ├── hvac-spare.yaml            # Spare board config
│   └── secrets.yaml.example       # Secrets template
├── hubitat/                       # Legacy Hubitat drivers (historical reference)
│   ├── KeenectLiteMaster.groovy
│   ├── KeenectLiteZone.groovy
│   └── ...
├── 3d/
│   └── hvac-controller-enclosure.scad  # 3D-printable controller enclosure
├── backups/                       # Legacy implementations
├── check_drift.py                 # Temperature sensor drift analysis tool
├── torture_test.sh                # ESP32 relay stress test
└── screenshots/                   # Dashboard screenshots
```

### Key files

- **`ha/pyscript/keenect_ha.py`** — All zone control logic: demand calculation, vent positioning, HVAC command dispatch, anomaly detection, cost tracking.
- **`esphome/hvac-controller.yaml`** — ESP32-S3 firmware with full wiring diagram, relay assignments, bypass logic, MQTT watchdog, and duct sensor processing.
- **`esphome/hvac-1st-floor.yaml`** — Heltec-based servo controller for the first-floor register.
- **`ha/packages/thermostats.yaml`** — Four `climate_template` entities, one per zone.
- **`3d/hvac-controller-enclosure.scad`** — OpenSCAD source for a 3D-printable DIN-rail enclosure sized for the ESP32-S3 + relay board.

---

## Legacy / History

Keenect originated as a Hubitat Elevation app — `KeenectLiteMaster` + `KeenectLiteZone` (Groovy). These are retained in `hubitat/` for historical reference.

The system was migrated to Home Assistant + pyscript to gain richer automation capabilities, better sensor integration (Ecowitt GW1000 weather gateway), and ESPHome-based hardware control. The Hubitat C-8 hub is still used as a Zigbee radio for the Keen vents, accessed through the Hubitat integration.

The original furnace controller was a Raspberry Pi running a Flask HTTP server (e.g., `192.168.1.123:5000` — legacy address, configure for your network). This is being replaced by the ESP32-S3 relay board described in this README.

---

## Contributing

This is a personal home automation project shared for reference. If you adapt it for your own system, keep the safety architecture intact:

- Keep the changeover relay bypass wired with NC to the fallback thermostat.
- Do not remove the heartbeat watchdog.
- Test furnace response after every code change before leaving the house.

Issues and pull requests are welcome for bugs, documentation improvements, or hardware variations.

---

## License

MIT License. See `LICENSE` for details.

This software controls physical heating and cooling equipment. The authors are not responsible for equipment damage, property damage, or any other harm resulting from its use. Use at your own risk. Always maintain a working fallback thermostat.
