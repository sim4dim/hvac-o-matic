# HVAC Hardware Documentation

## 1. HVAC Controller (ESP32-S3)

### Board

**MCU**: ESP32-S3-WROOM-1-N16R8 on a Freenove ESP32-S3 Breakout Board
- 16MB flash, 8MB PSRAM (octal mode, 80MHz)
- ESPHome firmware: `esphome/hvac-controller.yaml`
- Network: e.g., `hvac-controller.local` (configure hostname and IP for your network)

**Relay Board**: Kootek 8-Channel 5V Relay Module (active-low)
- Requires 10K pull-up resistors from each relay GPIO to 3.3V
- Without pull-ups, GPIOs float LOW during boot and relays fire briefly, causing spurious furnace activation

### GPIO Pin Map

| GPIO | Function | Relay | Notes |
|------|----------|-------|-------|
| 4 | Heat (W) | 1 | 24VAC to furnace W terminal |
| 5 | Cool (Y) | 2 | 24VAC to furnace Y terminal |
| 6 | Fan (G) | 3 | 24VAC to furnace G terminal |
| 7 | UV Light | 4 | On when any airflow active |
| 15 | W Changeover | 5 | NC=Pearl, NO=ESP32 |
| 16 | Y Changeover | 6 | NC=Pearl, NO=ESP32 |
| 17 | G Changeover | 7 | NC=Pearl, NO=ESP32 |
| 18 | Spare | 8 | Internal, unused |
| 11 | Bypass Switch | - | INPUT_PULLUP, physical toggle |
| 10 | Warning LED | - | On during bypass mode |
| 48 | RGB LED | - | WS2812 onboard status LED |
| 8 | I2C SDA | - | 100kHz |
| 9 | I2C SCL | - | 100kHz |

**Avoided GPIOs**: 0, 3, 19-20, 26-32, 45, 46 (PSRAM/flash lines, strapping pins, USB)

### Pull-Up Wiring

```
3.3V ──┬──┬──┬──┬──┬──┬──┬──
       R  R  R  R  R  R  R  R    (10K each)
       │  │  │  │  │  │  │  │
       4  5  6  7 15 16 17 18    (GPIO -> relay board IN1-IN8)
```

### I2C Devices

| Address | Device | Location | Measures |
|---------|--------|----------|----------|
| 0x76 | BME280 | Supply duct | Temperature, humidity, pressure |
| 0x77 | BME680 | Return duct | Temperature, humidity, pressure, VOC |
| 0x3C | SSD1306 | Enclosure front | 128x64 OLED display |

### 24VAC Thermostat Wiring

```
FURNACE                  RELAY BOARD                   PEARL THERMOSTAT
───────                  ───────────                   ────────────────

R (24V hot) ─────┬───────────────────────────────────── R
                 │
                 ├── Relay 1 COM ── NO ──> Relay 5 NO
                 ├── Relay 2 COM ── NO ──> Relay 6 NO
                 └── Relay 3 COM ── NO ──> Relay 7 NO

W (heat) ──────────── Relay 5 COM
                          NC <─────────────────────────── W
                          NO <── Relay 1 output

Y (cool) ──────────── Relay 6 COM
                          NC <─────────────────────────── Y
                          NO <── Relay 2 output

G (fan) ───────────── Relay 7 COM
                          NC <─────────────────────────── G
                          NO <── Relay 3 output

C (common) ──────────────────────────────────────────── C
```

### Bypass Architecture

```
                    ┌─────────────────┐
Pearl Thermostat ──>│ Changeover      │──> Furnace
                    │ Relays 5-7      │
ESP32 Relays 1-3 ──>│ (NC=Pearl,      │
                    │  NO=ESP32)      │
                    └─────────────────┘
```

**Normal mode** (changeover relays 5-7 energized, NO position): ESP32 relays 1-3 control the furnace W, Y, G lines. Pearl is powered via R and C but disconnected from furnace outputs.

**Bypass mode** (changeover relays 5-7 de-energized, NC position): Pearl thermostat controls the furnace directly. ESP32 control relays 1-3 are irrelevant — they are electrically disconnected from the furnace.

**Fail-safe** (ESP32 dead or unpowered): All relays de-energize to NC by default. Pearl resumes control automatically. No code required — this is a hardware property of the relay module.

Bypass is triggered by:
- Physical toggle switch on GPIO11
- HA command via ESPHome API
- 60-minute API watchdog (no HA connection)
- 5-minute keenect MQTT heartbeat watchdog
- Safety limits: 2-hour max run, supply temp > 160F, pressure delta > 10 hPa

### RGB Status LED (GPIO48)

| Color | State |
|-------|-------|
| Green solid | Normal, idle |
| Red solid | Heat active |
| Blue solid | Cool active |
| Purple solid | Bypass active |
| Orange flashing | Watchdog bypass |

### Power

24VAC from furnace transformer -> 24VAC-to-5VDC converter -> powers ESP32 and relay board.

---

## 2. 1st Floor Servo Controller (Heltec WiFi Kit 32 V3)

**Board**: Heltec WiFi Kit 32 V3 (ESP32)
- ESPHome firmware: `esphome/hvac-1st-floor.yaml`
- Static IP: `192.168.1.63` (configure for your network)
- Controls the first-floor supply register via a servo motor

### GPIO Pin Map

| GPIO | Function | Notes |
|------|----------|-------|
| 46 | Servo PWM | 50Hz LEDC output, auto-detach after 3s |
| 17 | I2C SDA | 400kHz |
| 18 | I2C SCL | 400kHz |
| 21 | OLED Reset | SH1106 128x64 display |

### Servo

- Range: 0 to 45 degrees
- 0 = closed, 45 = fully open
- ESPHome level mapping: `level = (angle / 90.0) - 1.0` (maps 0-45 into the -1.0 to -0.5 servo range)
- Auto-detach after 3 seconds to reduce servo heat and current draw
- Transition length: 2 seconds

### Display

- Model: SH1106 128x64 OLED at I2C address 0x3C
- Screensaver mode (most of the time): shows current angle at a randomized position to prevent burn-in
- Full info mode (5 seconds every 30 seconds): shows IP address and servo angle

---

## 3. Keen Smart Vents

- Protocol: Zigbee, paired to Hubitat C-8 coordinator
- Exposed in Home Assistant as `light.*` entities (NOT `cover.*`)
- Control: brightness percentage maps to vent opening (0% = closed, 100% = fully open)
- Entity IDs: `light.keen_ben`, `light.keen_gene`, `light.keen_mbr_1`, `light.keen_mbr_2`
- Note: the physical vent mechanism may not reach exactly 0% when commanded off; treat the commanded value as a target, not a guarantee

---

## 4. 3D Printed Enclosure

OpenSCAD file: `3d/hvac-controller-enclosure.scad`

Designed to hold:
- Freenove ESP32-S3 Breakout Board (87.6 x 83.2 x 22mm)
- Kootek 8-Channel Relay Module (139 x 56 x 19mm)
- TCA9548A I2C Multiplexer Hub (31.5 x 21.4 x 7mm)
- 0.96" SSD1306 OLED Display on front panel
- 12mm bypass button on front panel
- 5mm warning LED on front panel

Interior layout (relay terminals face the open/right side):

```
+-----------------------------------------------+
|  Relay Module (139 x 56)       [terminals] -->  (open right side)
|                                               |
|-----------------------------------------------|
|  Freenove Breakout (87.6 x 83.2) | I2C Hub   |
|                                   | (corner)  |
+-----------------------------------------------+
```

Wall mount: 4 keyhole slots on the back panel (2 top, 2 bottom), accepts standard screw heads up to 8mm diameter.

**Important**: Verify that the OpenSCAD dimensions match your specific board revisions before printing. Board dimensions vary between manufacturers and batches. Adjust the parametric variables at the top of the file as needed.

---

## 5. Safety Wiring Notes

- Use appropriate wire gauge for 24VAC connections (18 AWG minimum recommended for thermostat wiring runs).
- The changeover relay architecture is inherently fail-safe: a dead or unpowered ESP32 leaves all relays de-energized, which places them in the NC position, restoring Pearl thermostat control automatically.
- Mount the controller in an accessible location near the furnace for easy bypass switch access.
- Ensure adequate ventilation around the relay board — the relay coils generate heat under sustained load.
- **10K pull-up resistors on relay GPIOs are required.** Without them, GPIOs float during boot and the relays will briefly energize, potentially sending spurious heat or cool commands to the furnace on every restart.
- Short-cycle protections are enforced in firmware: heat is blocked for 120 seconds after the previous off event; cool is blocked for 300 seconds. These protect the furnace and compressor from rapid cycling.
- Supply temperature high limit (160F) and pressure delta limit (10 hPa) will force bypass and shut off outputs if exceeded.
