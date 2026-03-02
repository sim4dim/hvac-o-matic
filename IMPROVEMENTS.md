# HVAC Improvement Plan

Status: PENDING — waiting for ESPHome controller to be housed and physically installed.

## Context

This plan was generated from a multi-agent code review (Feb 2024) where an Architect agent (code quality focus) and Operator agent (safety/ops focus) independently reviewed the codebase, then debated priorities over MQTT. The Flask server at 192.168.1.123:5000 is the legacy furnace controller being actively replaced by the ESPHome ESP32-S3 controller. These improvements are for AFTER the migration, targeting gaps that exist regardless of transport layer.

## Priority 1: ESPHome Hardware Safety Layer

**Problem:** The ESPHome controller has zero runtime safety protections. Relays can stay energized indefinitely once turned on via API. If HA, pyscript, or the network dies, the furnace runs unchecked until the 60-minute API watchdog trips bypass — or forever if HA is up but pyscript is dead.

**File:** `esphome/hvac-controller.yaml`

**Changes needed:**

### 1a. Max-run timer (2 hour auto-off)
Add an `interval` block that tracks how long `heat_sw` or `cool_sw` has been continuously ON. If either exceeds 120 minutes, force all relays off, enter bypass mode, publish MQTT alert to `hvac/errors`.

### 1b. Short-cycle protection (compressor-safe)
Track last relay-off timestamp. Refuse to re-energize `cool_sw` within 5 minutes of last off (compressor refrigerant equalization). Refuse to re-energize `heat_sw` within 2 minutes of last off (heat exchanger thermal stress). Log blocked attempts.

### 1c. Supply temperature bounds
The BME280 reads supply air temp but nothing acts on it. Add:
- **High limit:** If supply temp > 160F while heating, immediately shut off heat relay + alert. (Lennox G61MPV heat exchanger limit.)
- **Low limit:** If supply temp < 80F after 10 minutes of continuous heating, alert for probable igniter/gas failure. Don't shut off (the furnace's own safety handles that) but notify.

### 1d. Duct pressure delta safety
The `pressure_delta` sensor exists but nothing acts on it. If differential pressure exceeds a threshold (TBD, needs baseline measurement) while furnace is running, shut off to prevent overpressure from closed vents. This is a backup to the 120-second vent closure delay.

**Why this is #1:** This is the ONLY safety layer that works when all software above it (HA, pyscript, network) has failed. Defense in depth.

## Priority 2: Pyscript-to-ESPHome Heartbeat Watchdog

**Problem:** The current ESPHome watchdog (line ~189) only checks API connectivity. If Home Assistant is running but pyscript has crashed, OOM'd, or failed to load, the watchdog sees a healthy API connection and never trips bypass. The furnace stays in whatever state pyscript last commanded — potentially running heat/cool indefinitely with no software oversight.

**Files:** `ha/pyscript/keenect_ha.py` + `esphome/hvac-controller.yaml`

**Changes needed:**

### 2a. Pyscript heartbeat publisher
In `keenect_ha.py`, add a `@time_trigger("period(now, 60s)")` function that publishes to MQTT topic `keenect/heartbeat` via `task.executor`. This confirms pyscript is alive and running periodic evaluations.

### 2b. ESPHome heartbeat subscriber
In `hvac-controller.yaml`, subscribe to `keenect/heartbeat` via MQTT. Reset a counter on each received message. If 5 consecutive heartbeats are missed (5 minutes), enter bypass mode (de-energize changeover relays, Pearl thermostat resumes control). Publish alert to `hvac/errors`.

### 2c. Replace or supplement the 60-minute API watchdog
The current 60-minute timeout (line ~189) is too long for a sole controller. Either:
- Reduce to 15 minutes as a fallback, OR
- Let the heartbeat watchdog (5 min) be the primary, keep API watchdog as secondary at 15 min.

**Why this is #2:** Covers the critical "HA alive, pyscript dead" failure mode that the current watchdog completely misses.

## Priority 3: Fix `_hvac_push` Failure Cascade

**Problem:** `_hvac_turn_on()` (lines ~506-518) and `_hvac_turn_off()` (lines ~524-536) in `keenect_ha.py` call `_hvac_push()` but ignore its boolean return value. If push fails (Flask/ESPHome unreachable), pyscript sets `main_state = "HEATING"` and `hvac_on = True` anyway. This creates a phantom state: software thinks furnace is on, but it's off (or vice versa). Zones never satisfy, anomaly detection fires 10+ minutes later at best, and the system is blind.

**File:** `ha/pyscript/keenect_ha.py`

**Changes needed:**

### 3a. Check return values
In `_hvac_turn_on()` and `_hvac_turn_off()`, check the return value of each `_hvac_push()` call. If any returns `False`, do NOT update `_st["main_state"]`, `_st["hvac_on"]`, or any other state variables. Return early and let the next eval cycle retry.

### 3b. Failure counter with escalating alerts
Add `_st["push_fail_count"]`. Increment on each failed push, reset on success.
- After 3 consecutive failures: create a `persistent_notification` in HA.
- After 5 consecutive failures: auto-disable keenect by turning off `input_boolean.keenect_enabled`. This prevents the system from continuing to operate with a broken control path.

### 3c. Sensor death alerting (related)
`_eval_zone()` returns `None` when a temperature sensor is unavailable (line ~744). The zone is silently skipped in `_eval_master()`. Add:
- Track consecutive `None` returns per zone in `_st["sensor_fail_count"]`.
- Alert after 3 consecutive failures (~45 seconds at 15s eval).
- Move zone vents to neutral position after 10 failures.
- If ALL 4 zones return `None` simultaneously: emergency shutoff + alert (probable Ecowitt GW1000 gateway failure).

## Secondary Items (lower priority, do when convenient)

### S1. Startup race condition
`on_startup()` (line ~1209) sets `hvac_on=False` but doesn't send off commands until after `_eval_master()` runs. If eval turns furnace on, then the safety sync sends off commands. Fix: send unconditional off commands BEFORE eval, in a `finally` block.

### S2. Startup Zigbee delay
After power outage, Keen vents need 60-90 seconds to rejoin the mesh. Commands sent during this window are silently dropped. Add a startup delay before first eval, or a verification pass 2 minutes after startup.

### S3. Cost tracking persistence
`_st["heat_runtime"]` and `_st["heat_cost"]` reset to 0 on every pyscript reload. Replace with HA `utility_meter` on a `binary_sensor.hvac_active`.

### S4. ESPHome changeover relay settle time
No delay between energizing changeover relays and first control command. Add 200ms settle delay in `bypass_sw.turn_off_action`.

### S5. Warmup sensor staleness
`_is_warming_up()` silently falls back to 120s fixed delay when duct sensors are offline. No alert that sensors aren't contributing. Add diagnostic attribute.
