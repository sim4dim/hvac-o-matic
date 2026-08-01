"""
Keenect HA - Keen Vent Zone Control for Home Assistant (pyscript)
Replaces Hubitat KeenectLiteMaster + KeenectLiteZone

Controls Keen smart vents via Hubitat integration (Zigbee radios on Hubitat).
HVAC furnace controlled directly via HTTP to Flask server (bypasses Hubitat).
First floor servo register controlled via ESPHome native API (number entity).

Version: 2.6.10 — two Away-mode fixes. FIX A: 2.6.9's "Away" attribution in
sensor.keenect_setpoint_log was racy — confirmed live 2026-07-31 17:21, a
manual setpoint change got logged as "Away" and the reclamp that immediately
corrected it got logged as "System" (inverted). Root cause: on_setpoint_change
calls _eval_master() (which ran _enforce_away_setpoints()) BEFORE resolving
its own attribution, and _enforce_away_setpoints() was stashing "Away" into
the SAME setpoint_user_pending[zone] slot on_climate_set_temperature uses for
real users — clobbering the real user's stash before the outer call could
read it, then the inner reclamp's own on_setpoint_change call found the slot
already emptied by the outer call and fell back to "System". Fixed by making
_enforce_away_setpoints() stop touching setpoint_user_pending entirely;
on_setpoint_change now resolves the real user first (untouched pending
mechanism), and only relabels a still-"System" result as "Away" when the zone
is currently in away mode AND the new value exactly equals the away setpoint
for that heat/cool attribute — deterministic, no shared timing-sensitive
slot. on_away_change's away→off restore writes get an analogous "Away off"
label via the pending stash (safe there since the zone's away boolean is
already off by the time those writes land, so _enforce_away_setpoints can't
clobber it). FIX B: _st["saved_setpoints"] (pre-away setpoints) was in-memory
only, so an HA restart mid-away-mode lost the true pre-away values and a
later away-off restored ZONE_DEFAULTS instead. Now persisted to its own
input_text.keenect_saved_setpoints (mirrors VENT_HEALTH_ENTITY's dedicated-
helper pattern exactly — STATE_ENTITY has no headroom left), saved whenever
saved_setpoints changes and restored at startup into memory only (applied
solely at the away→off transition, same as before).

Version: 2.6.9 — away-mode setpoint enforcement is now continuous, not just
at startup/toggle. Confirmed live 2026-07-31: away_gene (away_cool_setpoint=
78) let a manual setpoint change sit at 79 for 17 minutes, only silently
snapped back by a deploy-driven pyscript.reload landing on on_startup's
one-shot check — which also mislogged the correction as "System", sending
the user hunting for a rogue writer. _eval_master() now calls a new
_enforce_away_setpoints() right after the debounce gate is set (before any
zone's demand math runs), reasserting each away zone's heat/cool setpoint
every ~15s pass. Per-setpoint equality check keeps steady state silent (no
write, no log line, when already correct). The reclamp write re-enters
_eval_master() via on_setpoint_change, but that call always lands inside the
debounce window this pass just opened, so it can only spawn a follow-up
(task.unique-collapsed) — never recurse into the reclamp itself — and that
follow-up finds the value already correct and no-ops. Attributed as "Away"
in sensor.keenect_setpoint_log by stashing setpoint_user_pending[zone] before
each write, reusing the same pending mechanism on_climate_set_temperature
uses, instead of logging as unexplained "System". on_away_change's existing
save/restore-on-toggle-off behavior is unchanged.

Version: 2.6.8 — closes the debounce gap 2.6.7 FIX 4 left open: a debounced
_eval_master() call (periodic_eval landed <2s earlier) was silently dropped
with nothing to replay it, so the "immediate eval on setpoint change" fix did
nothing ~2s out of every 15s (measured live: 14.9s to COOLING, i.e. the old
behavior). A debounced call now spawns _eval_followup() via task.create(),
which waits out the remaining debounce window (re-checking, since a real eval
running in the meantime can push debounce_until further out) and then calls
_eval_master() once. task.unique("keenect_eval_followup") at its top collapses
a burst of these (e.g. a slider drag) down to the single most-recently-spawned
instance, so N rapid debounced calls still produce exactly one follow-up eval.
No change to the debounce gate itself or its re-entrancy guarantee.

Version: 2.6.7 — four fixes from a vent-traffic/staleness audit. (1) _set_vent()
now dedupes level==0 the same as level>0, so circ-reconciliation's per-15s
_set_vent(zn, 0) for circ-excluded zones (e.g. MBR) stops re-sending an
already-off vent every cycle (~100 redundant Zigbee commands/24min, confirmed).
_verify_vents()/_check_vent_health() are unaffected — both already clear/bypass
the vent_levels cache before any corrective re-send, so drift is still
corrected. (2) last_hvac_on_time/last_hvac_off_time (wall-clock epoch, not
monotonic) now persist across HA restarts with bounds-checked restore
(future/negative/>4h-old restored values are treated as unset), so
MIN_RUN_TIME/SHORT_CYCLE_* survive a restart instead of silently resetting;
on_startup still zeroes the restored on-time specifically when the live relay
disagrees with persisted main_state (true on-time unknown in that case, same
reasoning as 2.6.6). (3) _check_vent_health() no longer trusts a sensor's
last_updated (Hubitat re-stamps a fresh last_updated on a cached, days-stale
reading across restarts — confirmed on keen_mbr_2_pressure) or pressure alone
— it now tracks VALUE CHANGE per sensor (persisted in
input_text.keenect_vent_health_state) and calls a zone healthy if EITHER its
pressure or temperature sensor changed recently. (4) Setpoint edits now call
_eval_master() immediately from on_setpoint_change instead of waiting up to
15s for periodic_eval, reusing _eval_master()'s own debounce_until gate for
concurrency safety — no new locking added.

Version: 2.6.6 — circ-during-min-run-hold fix, in three parts (real incident
2026-07-31: HA restarted mid-MIN_RUN_TIME-hold, inside the ESPHome
re-initialization window). (1) _circ_start_effects() refuses to run while
hvac_on is True, and on_startup routes through it instead of a duplicated
inline loop. (2) on_startup no longer blindly forces hvac_on=False from the
persisted blob — it reconciles hvac_on/main_state against the live
switch.hvac_controller_heat/cool first, so (1)'s guard sees the truth after a
restart too; the heatOff/coolOff safety-sync pushes now require both a
confirmed "off" read and persisted-state agreement, to avoid the documented
short-cycle livelock on an ambiguous read. (3) hvac_on reconciliation still
fails toward False on an ambiguous read (protects the push_fail_count
auto-disable path), so it alone can't gate circulation-start; added
_relays_confirmed_off() (requires both relays read a definite "off") and used
it everywhere circulation actually starts. Full reasoning is in each
function's docstring/comments.

Version: 2.6.5 — NIT-1 concurrency hardening: _hvac_turn_on() now sets
_st["hvac_on"] = True before pushing the heat/cool/fan relays (each push is an
await point) instead of after, so a concurrent trigger (on_electricity_price_
change -> _reconcile_peak_lockout, or on_circ_change) that interleaves during
turn-on sees hvac_on=True and no-ops its fanOff/vent-close path instead of
racing in while heat/cool is being asserted. Reverted to False on push
failure. _hvac_turn_off() is unchanged (see NIT-1 review notes) — its flip to
False already happens after its pushes, which is the safe direction.

Version: 2.6.4 — on-peak circulation lockout: Periodic/Continuous circulation and
smart recirculation are suppressed during on-peak TOU windows (input_boolean.
circ_peak_lockout + sensor.electricity_price period=on-peak) to avoid running the
blower at peak electric rates. Fails open (circulation runs normally) if the price
signal is missing/unavailable. Heat/cool demand is never gated by this.
"""

import datetime as dt_mod
import json as json_mod
import time as time_mod
import re as re_mod
import urllib.request
import urllib.parse

# ---------------------------------------------------------------------------
# Zone configuration — hardcoded fallback (Phase 1)
# ---------------------------------------------------------------------------
_HARDCODED_ZONES = {
    "ben": {
        "thermostat": "climate.ben_s_room",
        "temp_sensor": "sensor.gw1000_temp_ch7",
        "vents": ["light.keen_ben"],
        "vent_type": "light",
        "health_sensors": ["sensor.keen_ben_pressure"],
        "health_sensors_temp": ["sensor.keen_ben_temperature"],
        "heat_min_vo": 15, "heat_max_vo": 100,
        "cool_min_vo": 15, "cool_max_vo": 100,
        "fan_vo": 30,
        "vent_control": "Aggressive",
    },
    "gene": {
        "thermostat": "climate.gene_s_room",
        "temp_sensor": "sensor.gw1000_temp_ch6",
        "vents": ["light.keen_gene"],
        "vent_type": "light",
        "health_sensors": ["sensor.keen_gene_pressure"],
        "health_sensors_temp": ["sensor.keen_gene_temperature"],
        "heat_min_vo": 15, "heat_max_vo": 100,
        "cool_min_vo": 15, "cool_max_vo": 100,
        "fan_vo": 30,
        "vent_control": "Aggressive",
    },
    "mbr": {
        "thermostat": "climate.master_bedroom",
        "temp_sensor": "sensor.gw1000_temp_ch4",
        "vents": ["light.keen_mbr_1", "light.keen_mbr_2"],
        "vent_type": "light",
        "health_sensors": ["sensor.keen_mbr_1_pressure", "sensor.keen_mbr_2_pressure"],
        "health_sensors_temp": ["sensor.keen_mbr_1_temperature", "sensor.keen_mbr_2_temperature"],
        "heat_min_vo": 15, "heat_max_vo": 100,
        "cool_min_vo": 15, "cool_max_vo": 100,
        "fan_vo": 30,
        "vent_control": "Aggressive",
    },
    "first_floor": {
        "thermostat": "climate.first_floor",
        "temp_sensor": "sensor.gw1000_indoor_temperature",
        "vents": ["number.hvac_1st_floor_register_servo_angle"],
        "vent_type": "number",
        "health_sensors": [],
        "health_sensors_temp": [],
        "heat_min_vo": 7, "heat_max_vo": 45,  # servo: 7°≈15% of 45° max
        "cool_min_vo": 7, "cool_max_vo": 45,
        "fan_vo": 15,  # ~33% of 45
        "vent_control": "Aggressive",
    },
}

_HARDCODED_PASSIVE = {
    "master_bath": {
        "temp_sensor": "sensor.master_bathroom_temperature_temperature",
    },
    "office": {
        "temp_sensor": "sensor.office_temperature_sonoff_temperature",
    },
    "basement": {
        "temp_sensor": "sensor.basement_sonoff_temperature",
    },
    "guest_bedroom": {
        "temp_sensor": "sensor.guest_bedroom_sonoff_temperature",
    },
}

_HARDCODED_DEFAULTS = {
    "ben": {"heat": 62, "cool": 76},
    "gene": {"heat": 62, "cool": 76},
    "mbr": {"heat": 64, "cool": 76},
    "first_floor": {"heat": 69, "cool": 76},
}

# ---------------------------------------------------------------------------
# Zone configuration — loaded from HA helpers at startup
# ---------------------------------------------------------------------------
ZONES = {}
PASSIVE_ZONES = {}
ZONE_DEFAULTS = {}


def _populate_dropdowns():
    """Populate config dropdown options from available HA entities."""
    try:
        # Temperature sensors
        all_sensors = state.names(domain="sensor")
        temp_sensors = sorted([s for s in all_sensors
                               if "temperature" in s.lower() or "temp" in s.lower()])
        # Also include any sensor with device_class temperature
        for s in all_sensors:
            attrs = state.getattr(s)
            if attrs and attrs.get("device_class") == "temperature" and s not in temp_sensors:
                temp_sensors.append(s)
        temp_sensors = sorted(set(temp_sensors))

        # Thermostats
        thermostats = sorted(state.names(domain="climate"))

        # Vents — lights (Keen vents) and numbers (servo registers)
        lights = sorted(state.names(domain="light"))
        numbers = sorted(state.names(domain="number"))
        vent_options = sorted(lights + numbers)

        for slot in range(1, 5):
            pfx = f"keenect_zone_{slot}"
            input_select.set_options(
                entity_id=f"input_select.{pfx}_temp_sensor",
                options=["(none)"] + temp_sensors)
            input_select.set_options(
                entity_id=f"input_select.{pfx}_thermostat",
                options=["(none)"] + thermostats)
            input_select.set_options(
                entity_id=f"input_select.{pfx}_vent_1",
                options=["(none)"] + vent_options)
            input_select.set_options(
                entity_id=f"input_select.{pfx}_vent_2",
                options=["(none)"] + vent_options)

        for slot in range(1, 5):
            pfx = f"keenect_passive_{slot}"
            input_select.set_options(
                entity_id=f"input_select.{pfx}_temp_sensor",
                options=["(none)"] + temp_sensors)

        log.info(f"keenect: populated dropdowns — {len(temp_sensors)} temp sensors, "
                 f"{len(thermostats)} thermostats, {len(vent_options)} vent entities")
    except Exception as e:
        log.error(f"keenect: failed to populate dropdowns: {e}")


def _load_zone_config():
    """Load zone configuration from HA helper entities."""
    global ZONES, PASSIVE_ZONES, ZONE_DEFAULTS
    zones = {}
    passive = {}
    defaults = {}

    all_sensor_names = state.names(domain="sensor")

    for slot in range(1, 5):
        pfx = f"keenect_zone_{slot}"
        enabled = state.get(f"input_boolean.{pfx}_enabled")
        if enabled != "on":
            continue
        name = str(state.get(f"input_text.{pfx}_name") or "").strip().lower().replace(" ", "_")
        if not name or name in ("unknown", ""):
            log.warning(f"keenect: zone slot {slot} enabled but no name set")
            continue

        temp_sensor = state.get(f"input_select.{pfx}_temp_sensor")
        thermostat = state.get(f"input_select.{pfx}_thermostat")
        vent_1 = state.get(f"input_select.{pfx}_vent_1")
        vent_2 = state.get(f"input_select.{pfx}_vent_2")
        vent_type = state.get(f"input_select.{pfx}_vent_type") or "light"
        vent_control = state.get(f"input_select.{pfx}_vent_control") or "Aggressive"

        if not temp_sensor or temp_sensor == "(none)":
            log.warning(f"keenect: zone '{name}' has no temp sensor")
            continue
        if not thermostat or thermostat == "(none)":
            log.warning(f"keenect: zone '{name}' has no thermostat")
            continue

        vents = [v for v in [vent_1, vent_2] if v and v != "(none)"]
        if not vents:
            log.warning(f"keenect: zone '{name}' has no vents")
            continue

        # Auto-discover health sensors by convention: light.keen_X -> sensor.keen_X_pressure
        # / sensor.keen_X_temperature. Both are used for liveness (see
        # _check_vent_health) — pressure alone was found to under-report
        # freshness since both are timer-driven, continuously-drifting signals.
        health_sensors = []
        health_sensors_temp = []
        for v in vents:
            if vent_type == "light":
                vent_suffix = v.replace("light.", "")
                health_entity = f"sensor.{vent_suffix}_pressure"
                if health_entity in all_sensor_names:
                    health_sensors.append(health_entity)
                temp_entity = f"sensor.{vent_suffix}_temperature"
                if temp_entity in all_sensor_names:
                    health_sensors_temp.append(temp_entity)

        heat_min = int(float(state.get(f"input_number.{pfx}_heat_min_vo") or 15))
        heat_max = int(float(state.get(f"input_number.{pfx}_heat_max_vo") or 100))
        cool_min = int(float(state.get(f"input_number.{pfx}_cool_min_vo") or 15))
        cool_max = int(float(state.get(f"input_number.{pfx}_cool_max_vo") or 100))
        fan_vo = int(float(state.get(f"input_number.{pfx}_fan_vo") or 30))
        heat_def = int(float(state.get(f"input_number.{pfx}_heat_default") or 62))
        cool_def = int(float(state.get(f"input_number.{pfx}_cool_default") or 76))

        zones[name] = {
            "thermostat": thermostat,
            "temp_sensor": temp_sensor,
            "vents": vents,
            "vent_type": vent_type,
            "health_sensors": health_sensors,
            "health_sensors_temp": health_sensors_temp,
            "heat_min_vo": heat_min,
            "heat_max_vo": heat_max,
            "cool_min_vo": cool_min,
            "cool_max_vo": cool_max,
            "fan_vo": fan_vo,
            "vent_control": vent_control,
        }
        defaults[name] = {"heat": heat_def, "cool": cool_def}

    # Passive zones
    for slot in range(1, 5):
        pfx = f"keenect_passive_{slot}"
        enabled = state.get(f"input_boolean.{pfx}_enabled")
        if enabled != "on":
            continue
        name = str(state.get(f"input_text.{pfx}_name") or "").strip().lower().replace(" ", "_")
        if not name or name in ("unknown", ""):
            continue
        temp_sensor = state.get(f"input_select.{pfx}_temp_sensor")
        if not temp_sensor or temp_sensor == "(none)":
            continue
        passive[name] = {"temp_sensor": temp_sensor}

    ZONES = zones
    PASSIVE_ZONES = passive
    ZONE_DEFAULTS = defaults

    log.info(f"keenect: loaded {len(ZONES)} active zones: {list(ZONES.keys())}")
    log.info(f"keenect: loaded {len(PASSIVE_ZONES)} passive zones: {list(PASSIVE_ZONES.keys())}")
    return len(ZONES) > 0


def _build_derived_maps():
    """Build setpoint map, zone codes, trigger entity lists from loaded config."""
    global _SETPOINT_MAP, _ZONE_TO_CODE, _CODE_TO_ZONE
    global _HEALTH_SENSOR_CODE, _HEALTH_CODE_SENSOR
    _SETPOINT_MAP = {}
    for zn in ZONES:
        _SETPOINT_MAP[f"input_number.{zn}_heat_setpoint"] = (zn, "heat")
        _SETPOINT_MAP[f"input_number.{zn}_cool_setpoint"] = (zn, "cool")

    _ZONE_TO_CODE = {}
    _CODE_TO_ZONE = {}
    used = set()
    for zn in ZONES:
        display = zn.replace("_", " ").title()
        code = display[0].lower()
        i = 1
        while code in used or not code.isalpha():
            if i >= len(display):
                code = zn[0]  # ultimate fallback to raw zone name char
                break
            code = display[i].lower()
            i += 1
        used.add(code)
        _ZONE_TO_CODE[display] = code
        _CODE_TO_ZONE[code] = display

    # Vent-health liveness codes (FIX 3): <zone_code><vent_index><p|t>, e.g.
    # "m2t" = mbr's 2nd vent's temperature sensor. Built from the live ZONES
    # config so it works whether zones come from HA helpers or the hardcoded
    # fallback, rather than hardcoding sensor entity ids here.
    _HEALTH_SENSOR_CODE = {}
    _HEALTH_CODE_SENSOR = {}
    for zn, zone in ZONES.items():
        zcode = _ZONE_TO_CODE.get(zn.replace("_", " ").title(), zn[0])
        for i, sid in enumerate(zone.get("health_sensors", [])):
            code = f"{zcode}{i + 1}p"
            _HEALTH_SENSOR_CODE[sid] = code
            _HEALTH_CODE_SENSOR[code] = sid
        for i, sid in enumerate(zone.get("health_sensors_temp", [])):
            code = f"{zcode}{i + 1}t"
            _HEALTH_SENSOR_CODE[sid] = code
            _HEALTH_CODE_SENSOR[code] = sid


def _persist_zone_config():
    """Save current input_select values to input_text for restart survival."""
    for slot in range(1, 5):
        pfx = f"keenect_zone_{slot}"
        data = {
            "ts": state.get(f"input_select.{pfx}_temp_sensor") or "(none)",
            "th": state.get(f"input_select.{pfx}_thermostat") or "(none)",
            "v1": state.get(f"input_select.{pfx}_vent_1") or "(none)",
            "v2": state.get(f"input_select.{pfx}_vent_2") or "(none)",
        }
        val = json_mod.dumps(data, separators=(',', ':'))
        input_text.set_value(entity_id=f"input_text.{pfx}_persist", value=val)

    for slot in range(1, 5):
        pfx = f"keenect_passive_{slot}"
        data = {
            "ts": state.get(f"input_select.{pfx}_temp_sensor") or "(none)",
        }
        val = json_mod.dumps(data, separators=(',', ':'))
        input_text.set_value(entity_id=f"input_text.{pfx}_persist", value=val)

    log.info("keenect: zone config persisted to input_text backing store")


def _restore_zone_selects():
    """Restore input_select values from persisted input_text after restart."""
    restored = 0
    for slot in range(1, 5):
        pfx = f"keenect_zone_{slot}"
        raw = state.get(f"input_text.{pfx}_persist")
        if not raw or raw in ("unknown", "", "unavailable"):
            continue
        try:
            data = json_mod.loads(raw)
            for key, entity_suffix in [("ts", "temp_sensor"), ("th", "thermostat"),
                                        ("v1", "vent_1"), ("v2", "vent_2")]:
                val = data.get(key, "(none)")
                if val and val != "(none)":
                    try:
                        input_select.select_option(
                            entity_id=f"input_select.{pfx}_{entity_suffix}",
                            option=val)
                        restored += 1
                    except Exception as e:
                        log.warning(f"keenect: failed to restore {pfx}_{entity_suffix}={val}: {e}")
        except Exception as e:
            log.warning(f"keenect: failed to parse {pfx}_persist: {e}")

    for slot in range(1, 5):
        pfx = f"keenect_passive_{slot}"
        raw = state.get(f"input_text.{pfx}_persist")
        if not raw or raw in ("unknown", "", "unavailable"):
            continue
        try:
            data = json_mod.loads(raw)
            val = data.get("ts", "(none)")
            if val and val != "(none)":
                try:
                    input_select.select_option(
                        entity_id=f"input_select.{pfx}_temp_sensor",
                        option=val)
                    restored += 1
                except Exception as e:
                    log.warning(f"keenect: failed to restore {pfx}_temp_sensor={val}: {e}")
        except Exception as e:
            log.warning(f"keenect: failed to parse {pfx}_persist: {e}")

    log.info(f"keenect: restored {restored} dropdown selections from backing store")


# HVAC Flask server - direct HTTP control (bypasses Hubitat driver)
HVAC_SERVER = "http://192.168.1.123:5000"
# Servo register now controlled via ESPHome native API (number entity)
# Button-to-URL path mapping (matches HVACdriver.groovy push commands)
HVAC_COMMANDS = {
    1: "/off",        # off
    2: "/HEAT/on",    # heatOn
    3: "/HEAT/off",   # heatOff
    4: "/COOL/on",    # coolOn
    5: "/COOL/off",   # coolOff
    6: "/FAN/on",     # fanOn
    7: "/FAN/off",    # fanOff
}

# ESPHome controller mirror: button → switch service calls
ESPHOME_MIRROR_MAP = {
    1: [("turn_off", "switch.hvac_controller_heat"), ("turn_off", "switch.hvac_controller_cool"), ("turn_off", "switch.hvac_controller_fan")],
    2: [("turn_on", "switch.hvac_controller_heat")],
    3: [("turn_off", "switch.hvac_controller_heat")],
    4: [("turn_on", "switch.hvac_controller_cool")],
    5: [("turn_off", "switch.hvac_controller_cool")],
    6: [("turn_on", "switch.hvac_controller_fan")],
    7: [("turn_off", "switch.hvac_controller_fan")],
}

OUTDOOR_TEMP_ENTITY = "sensor.weewx_outdoor_temperature"

# Supply/return duct sensors (ESPHome hvac-controller)
SUPPLY_TEMP_ENTITY = "sensor.hvac_controller_supply_temperature"
RETURN_TEMP_ENTITY = "sensor.hvac_controller_return_temperature"
# Warmup: hold vents at minimum until supply air is conditioned
WARMUP_HEAT_DELTA = 15  # supply must be this much warmer than return (°F)
WARMUP_COOL_DELTA = 10  # supply must be this much cooler than return (°F)
WARMUP_FIXED_DELAY = 120  # fallback/max delay (seconds)

# Safety: short-cycle protection and sensor failure thresholds
SHORT_CYCLE_COOL_MIN = 300  # minimum seconds between cool off and next cool on
SHORT_CYCLE_HEAT_MIN = 120  # minimum seconds between heat off and next heat on
MIN_RUN_TIME = 180          # minimum seconds HVAC must run before turning off
SENSOR_FAIL_ALERT = 3       # consecutive None readings before alert (~45s at 15s eval)
SENSOR_FAIL_NEUTRAL = 10    # consecutive None readings before moving vents to neutral (~2.5min)

# Setpoint change tracking — maps input_number to (zone, heat/cool)
# Populated dynamically by _build_derived_maps() at startup
_SETPOINT_MAP = {
    "input_number.ben_heat_setpoint": ("ben", "heat"),
    "input_number.ben_cool_setpoint": ("ben", "cool"),
    "input_number.gene_heat_setpoint": ("gene", "heat"),
    "input_number.gene_cool_setpoint": ("gene", "cool"),
    "input_number.mbr_heat_setpoint": ("mbr", "heat"),
    "input_number.mbr_cool_setpoint": ("mbr", "cool"),
    "input_number.first_floor_heat_setpoint": ("first_floor", "heat"),
    "input_number.first_floor_cool_setpoint": ("first_floor", "cool"),
}
_SETPOINT_LOG_MAX = 20
STATE_ENTITY = "input_text.keenect_persisted_state"
SETPOINT_LOG_ENTITY = "input_text.keenect_setpoint_log_data"
# Dedicated entity for vent-health liveness tracking (FIX 3) — kept separate
# from STATE_ENTITY, which was observed at 233/255 chars live with almost no
# headroom left. NOTE: like STATE_ENTITY/SETPOINT_LOG_ENTITY, this input_text
# helper is created via the HA WS API at deploy time, not via package YAML
# (see ha/packages/irrigation.yaml's comment on the same pattern) — it must
# exist (input_text, max: 255) before this will actually persist; until then
# _save_vent_health_state() fails silently (debug log only) and liveness
# tracking just restarts fresh each reload, same as before this fix.
VENT_HEALTH_ENTITY = "input_text.keenect_vent_health_state"

# Dedicated entity for away-mode pre-away setpoint persistence (FIX B,
# 2.6.10) — same rationale/pattern as VENT_HEALTH_ENTITY directly above:
# STATE_ENTITY has no headroom left, so _st["saved_setpoints"] (populated
# only for zones currently in away mode) gets its own tiny input_text, saved/
# restored by _save_saved_setpoints_state()/_restore_saved_setpoints_state().
# Also created via the HA WS API at deploy time, not package YAML — until it
# exists, save fails silently (debug log only), identical degrade to
# _save_vent_health_state().
SAVED_SETPOINTS_ENTITY = "input_text.keenect_saved_setpoints"

# Zone name <-> compact code for setpoint log persistence (255 char limit)
# Populated dynamically by _build_derived_maps() at startup; static values are fallback
_ZONE_TO_CODE = {"Ben": "b", "Gene": "g", "Mbr": "m", "First Floor": "f"}
_CODE_TO_ZONE = {"b": "Ben", "g": "Gene", "m": "Mbr", "f": "First Floor"}

# Sensor entity id <-> compact code for vent-health liveness persistence.
# Populated dynamically by _build_derived_maps() at startup; static values
# are fallback (matches the hardcoded ZONES fallback sensor ids).
_HEALTH_SENSOR_CODE = {
    "sensor.keen_ben_pressure": "b1p", "sensor.keen_ben_temperature": "b1t",
    "sensor.keen_gene_pressure": "g1p", "sensor.keen_gene_temperature": "g1t",
    "sensor.keen_mbr_1_pressure": "m1p", "sensor.keen_mbr_1_temperature": "m1t",
    "sensor.keen_mbr_2_pressure": "m2p", "sensor.keen_mbr_2_temperature": "m2t",
}
_HEALTH_CODE_SENSOR = {v: k for k, v in _HEALTH_SENSOR_CODE.items()}

# Vent health check - if pressure sensor hasn't reported in this many seconds,
# the vent is likely offline. Pressure reports every ~5 min, so 30 min = very stale.
VENT_STALE_SECONDS = 1800  # 30 minutes

# Keys persisted via input_text (survive HA restarts)
_PERSIST_KEYS = [
    "main_state", "hvac_on", "recirc_active", "zone_states",
    "hvac_off_time", "vents_closed_after_off",
    "sensor_fail_parked", "all_none_count",
    "last_hvac_on_time", "last_hvac_off_time",
]

# Sanity bound for restoring last_hvac_on_time/last_hvac_off_time (both
# wall-clock epoch via time.time(), NOT monotonic — see _load_state()). A
# restored timestamp older than this is treated as expired/unset rather than
# arming a stale MIN_RUN_TIME/SHORT_CYCLE guard.
_RESTORED_TS_MAX_AGE = 4 * 3600  # 4 hours

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------
_st = {
    "main_state": "IDLE",
    "zone_states": {},
    "vent_levels": {},
    "hvac_on": False,
    "recirc_active": False,
    "last_all_idle": None,
    "debounce_until": 0.0,
    "hvac_off_time": None,      # timestamp when HVAC was turned off
    "vents_closed_after_off": True,  # whether vents were closed after last HVAC off
    "_last_persisted": None,    # snapshot of last persisted state
    "setpoint_log": [],         # recent setpoint changes for activity panel
    # Cost tracking (monotonically increasing, reset by HA utility_meter)
    "last_cost_time": None,     # timestamp of last cost accumulation
    "heat_runtime": 0.0,        # cumulative heating hours
    "heat_cost": 0.0,           # cumulative heating cost ($)
    "cool_runtime": 0.0,        # cumulative cooling hours
    "cool_cost": 0.0,           # cumulative cooling cost ($)
    "warmup_start": None,       # timestamp when HVAC cycle started (for vent delay)
    # Safety: push failure tracking
    "push_fail_count": 0,
    # Safety: per-zone consecutive sensor failure counts
    "sensor_fail_count": {},
    # Safety: True when any vent was left at fan_vo due to sensor-failure neutral
    "sensor_fail_parked": False,
    # Safety: short-cycle and min-run protection timestamps
    "last_hvac_on_time": 0,
    "last_hvac_off_time": 0,
    # Warmup sensor health flag
    "_warmup_sensors_ok": True,
    # Outdoor temp freshness tracking
    "outdoor_temp_last_value": None,   # last seen outdoor temp value
    "outdoor_temp_last_changed": 0.0,  # timestamp when value last changed
    # Safety: consecutive all-None eval counter (debounce emergency shutoff)
    "all_none_count": 0,
    # ETA: per-zone rolling (timestamp, temp) buffer for rate/ETA computation
    "zone_temp_history": {},
    # Setpoint attribution: zone -> (user_name, timestamp) captured from climate.set_temperature
    # so the follow-on input_number change can be attributed to the real user.
    "setpoint_user_pending": {},
    # Vent-health liveness (FIX 3): sensor_id -> (rounded_value, wallclock_when_last_changed)
    "sensor_liveness": {},
}


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------
def _persist_snapshot():
    """Return a string of the persisted fields for change detection."""
    return json_mod.dumps({k: _st.get(k) for k in _PERSIST_KEYS}, sort_keys=True)


def _save_state():
    """Persist critical state to an HA input_text entity."""
    # Use compact keys to fit in 255 chars
    # Collect number/servo vent levels (lights report their own state)
    sv = {}
    for zn, zone in ZONES.items():
        if zone.get("vent_type") in ("servo", "number"):
            for vid in zone["vents"]:
                key = f"{zn}:{vid}"
                val = _st["vent_levels"].get(key)
                if val is not None:
                    sv[key] = val
    data = {
        "ms": _st["main_state"],
        "ho": 1 if _st["hvac_on"] else 0,
        "ra": 1 if _st["recirc_active"] else 0,
        "zs": {k: v[:1] for k, v in _st["zone_states"].items()},  # I/H/C/F
        # FIX 2 (2.6.7): rounded to int seconds — this is a 120-300s delay
        # timer, sub-second precision was never needed and this recovers a
        # few chars of the now-tighter 255-char budget (see "lon"/"lof" below).
        "ot": int(_st["hvac_off_time"]) if _st["hvac_off_time"] is not None else None,
        "vc": 1 if _st["vents_closed_after_off"] else 0,
        "sv": sv,
        "sfp": 1 if _st.get("sensor_fail_parked") else 0,  # L8: sensor_fail_parked
        "anc": _st.get("all_none_count", 0),               # L8: all_none_count
        # FIX 2 (2.6.7): short-cycle/min-run timestamps, integer epoch seconds
        # (sub-second precision isn't needed against 120-300s thresholds).
        "lon": int(_st.get("last_hvac_on_time", 0) or 0),
        "lof": int(_st.get("last_hvac_off_time", 0) or 0),
    }
    # P7: Include cost tracking counters (heating/cooling runs and cycles)
    cost_keys = {
        "hr": round(_st.get("heat_runtime", 0), 4),
        "hc": round(_st.get("heat_cost", 0), 2),
        "cr": round(_st.get("cool_runtime", 0), 4),
        "cc": round(_st.get("cool_cost", 0), 2),
    }
    data.update(cost_keys)
    try:
        val = json_mod.dumps(data, separators=(",", ":"))
        # Safety: input_text has 255 char limit — drop cost keys if too long
        if len(val) > 255:
            log.warning(f"keenect: state JSON {len(val)} chars > 255, dropping cost keys")
            for ck in ("hr", "hc", "cr", "cc"):
                data.pop(ck, None)
            val = json_mod.dumps(data, separators=(",", ":"))
        input_text.set_value(entity_id=STATE_ENTITY, value=val)
    except Exception as e:
        log.error(f"keenect: failed to save state: {e}")


def _save_if_changed():
    """Save state only when persisted fields have changed."""
    snap = _persist_snapshot()
    if snap != _st.get("_last_persisted"):
        _save_state()
        _st["_last_persisted"] = snap


# Zone state abbreviation mapping
_ZS_MAP = {"I": "IDLE", "H": "HEATING", "C": "COOLING", "F": "FAN ONLY"}


def _load_state():
    """Restore state from the HA input_text entity after restart."""
    try:
        raw = state.get(STATE_ENTITY)
        if raw in (None, "", "{}", "unknown", "unavailable"):
            log.info("keenect: no saved state (first run)")
            return
        data = json_mod.loads(raw)
        _st["main_state"] = data.get("ms", "IDLE")
        _st["hvac_on"] = bool(data.get("ho", 0))
        _st["recirc_active"] = bool(data.get("ra", 0))
        _st["zone_states"] = {
            k: _ZS_MAP.get(v, "IDLE") for k, v in data.get("zs", {}).items()
        }
        _st["hvac_off_time"] = data.get("ot")
        _st["vents_closed_after_off"] = bool(data.get("vc", 1))
        # Restore number/servo vent levels (lights report their own state)
        for key, val in data.get("sv", {}).items():
            _st["vent_levels"][key] = val
        # L8: Restore safety flags
        _st["sensor_fail_parked"] = bool(data.get("sfp", 0))
        _st["all_none_count"] = int(data.get("anc", 0))
        # FIX 2 (2.6.7): Restore short-cycle/min-run timestamps. Both are
        # wall-clock epoch via time.time() (NOT time.monotonic(), which would
        # be garbage after a restart) so restoring the raw value is valid —
        # but bound defensively: a restored timestamp that is in the future,
        # negative, or older than _RESTORED_TS_MAX_AGE is treated as
        # expired/unset (0). A wrongly-"recent" last_hvac_on_time would BLOCK
        # a needed shutoff (MIN_RUN_TIME), which is worse than the bug being
        # fixed, so anything implausible fails toward 0 (no guard armed).
        now_restore = time_mod.time()
        for src_key, dst_key in (("lon", "last_hvac_on_time"), ("lof", "last_hvac_off_time")):
            raw_ts = data.get(src_key, 0)
            try:
                ts = float(raw_ts)
            except (TypeError, ValueError):
                ts = 0
            if ts <= 0 or ts > now_restore + 60 or (now_restore - ts) > _RESTORED_TS_MAX_AGE:
                if ts:
                    log.warning(
                        f"keenect: discarding implausible restored {dst_key}={ts} "
                        f"(now={now_restore:.0f}) — treating as unset"
                    )
                ts = 0
            _st[dst_key] = ts
        # P7: Restore cost tracking counters
        _st["heat_runtime"] = data.get("hr", 0.0)
        _st["heat_cost"] = data.get("hc", 0.0)
        _st["cool_runtime"] = data.get("cr", 0.0)
        _st["cool_cost"] = data.get("cc", 0.0)
        _st["_last_persisted"] = _persist_snapshot()
        log.info(
            f"keenect: restored state - hvac_on={_st['hvac_on']} "
            f"main={_st['main_state']} recirc={_st['recirc_active']} "
            f"zones={_st['zone_states']}"
        )
    except Exception as e:
        log.warning(f"keenect: failed to load state: {e}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _float(entity_id, default=None):
    """Read entity state as float."""
    try:
        val = state.get(entity_id)
    except Exception:
        return default
    if val in (None, "unknown", "unavailable", ""):
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _enabled():
    return state.get("input_boolean.keenect_enabled") == "on"


def _hvac_mode():
    return state.get("input_select.hvac_mode") or "HEAT"


def _vent_delay():
    return int(_float("input_number.vent_closure_delay", 120))


def _recirc_delay_min():
    return int(_float("input_number.recirculation_delay", 15))


def _recirc_enabled():
    return state.get("input_boolean.enable_recirculation") == "on"


def _peak_lockout():
    """True only when the on-peak circulation lockout is armed AND we are
    currently inside an on-peak TOU window (sensor.electricity_price is owned
    by the housekeeping project — read-only here).

    FAILS OPEN: if the lockout toggle is off, or the price sensor/attribute is
    missing, unavailable, or errors, returns False (circulation runs normally).
    A broken/absent housekeeping sensor must never suppress our fan behavior.
    """
    try:
        if state.get("input_boolean.circ_peak_lockout") != "on":
            return False
        attrs = state.getattr("sensor.electricity_price")
        period = attrs.get("period") if attrs else None
    except Exception as e:
        # ANY read failure (missing toggle entity, absent/unavailable price
        # sensor, missing attribute) must fail open — a broken helper must
        # never crash the master control loop or suppress our fan behavior.
        log.warning(f"keenect: peak lockout read failed, failing open: {e}")
        return False
    if period is None:
        return False
    return period == "on-peak"


def _circ_enabled():
    return state.get("input_boolean.enable_circulation") == "on" and not _peak_lockout()


def _zone_circ_excluded(zn):
    """Check if a zone has opted out of circulation via input_boolean."""
    return state.get(f"input_boolean.circ_optout_{zn}") == "on"


def _hysteresis():
    return _float("input_number.keenect_hysteresis", 0.5)


def _cool_lockout_temp():
    return _float("input_number.cool_lockout_temp", 50.0)


_user_cache = {}


def _build_user_cache():
    """Build user_id -> name cache from person entities and HA auth users."""
    # Person entities (reliable, covers real users)
    try:
        for eid in state.names("person"):
            attrs = state.getattr(eid)
            uid = attrs.get("user_id") if attrs else None
            if uid:
                _user_cache[uid] = attrs.get("friendly_name", eid.split(".")[-1].title())
    except Exception as e:
        log.debug(f"keenect: person cache failed: {e}")

    # HA auth users (covers admin/system accounts without person entities)
    try:
        users = hass.auth.async_get_users()
        for u in users:
            if u.id not in _user_cache and u.name:
                _user_cache[u.id] = u.name
    except Exception as e:
        log.debug(f"keenect: auth user cache skipped: {e}")

    if _user_cache:
        log.info(f"keenect: user cache built with {len(_user_cache)} entries")


def _resolve_user(user_id):
    """Resolve HA user_id to friendly name."""
    if not user_id:
        return "System"
    name = _user_cache.get(user_id)
    if name:
        return name
    # Lazy lookup for users added after startup
    try:
        for eid in state.names("person"):
            attrs = state.getattr(eid)
            if attrs and attrs.get("user_id") == user_id:
                name = attrs.get("friendly_name", eid.split(".")[-1].title())
                _user_cache[user_id] = name
                return name
    except Exception:
        pass
    return "Admin"


def _update_setpoint_log_sensor():
    """Publish setpoint change log as a sensor entity."""
    entries = _st.get("setpoint_log", [])
    last = entries[0] if entries else None
    summary = (
        f"{last['user']}: {last['zone']} {last['type']} → {last['new']}°F"
        if last else "No changes"
    )
    state.set("sensor.keenect_setpoint_log", summary, {
        "friendly_name": "Setpoint Changes",
        "icon": "mdi:history",
        "entries": entries,
    })


def _save_setpoint_log():
    """Persist setpoint log to input_text (survives HA restarts).
    Compact pipe-delimited format to fit 255 char limit (~8 entries)."""
    entries = _st.get("setpoint_log", [])
    parts = []
    for e in entries:
        zc = _ZONE_TO_CODE.get(e["zone"], e["zone"][:1].lower())
        tc = e["type"][0].lower()
        ov = int(e["old"]) if e["old"] == int(e["old"]) else e["old"]
        nv = int(e["new"]) if e["new"] == int(e["new"]) else e["new"]
        u = e.get("user", "?")[:8]
        parts.append(f"{e['time']}|{zc}|{tc}|{ov}|{nv}|{u}")
    val = ";".join(parts)
    while len(val) > 255 and ";" in val:
        val = val[:val.rfind(";")]
    try:
        input_text.set_value(entity_id=SETPOINT_LOG_ENTITY, value=val if val else " ")
    except Exception as e:
        log.warning(f"keenect: failed to save setpoint log: {e}")


def _restore_setpoint_log():
    """Restore setpoint log — try sensor attributes (pyscript reload), then input_text (HA restart)."""
    # Try sensor attributes first (richer data, survives pyscript reload)
    try:
        prev = state.getattr("sensor.keenect_setpoint_log")
        if prev and "entries" in prev:
            entries = prev["entries"]
            if entries and isinstance(entries, list) and len(entries) > 0:
                _st["setpoint_log"] = entries[:_SETPOINT_LOG_MAX]
                log.info(f"keenect: restored {len(_st['setpoint_log'])} setpoint log entries from sensor")
                return
    except Exception as e:
        log.warning(f"keenect: sensor setpoint restore failed: {e}")

    # Fallback: input_text (compact format, survives HA restart)
    try:
        raw = state.get(SETPOINT_LOG_ENTITY)
        if raw in (None, "", " ", "unknown", "unavailable"):
            return
        entries = []
        for part in raw.split(";"):
            fields = part.split("|")
            if len(fields) >= 6:
                entries.append({
                    "time": fields[0],
                    "zone": _CODE_TO_ZONE.get(fields[1], fields[1].title()),
                    "type": "Heat" if fields[2] == "h" else "Cool",
                    "old": int(float(fields[3])) if float(fields[3]) % 1 == 0 else float(fields[3]),
                    "new": int(float(fields[4])) if float(fields[4]) % 1 == 0 else float(fields[4]),
                    "user": fields[5],
                })
        if entries:
            _st["setpoint_log"] = entries[:_SETPOINT_LOG_MAX]
            log.info(f"keenect: restored {len(entries)} setpoint log entries from input_text")
    except Exception as e:
        log.warning(f"keenect: input_text setpoint restore failed: {e}")


# ---------------------------------------------------------------------------
# HVAC furnace control
# ---------------------------------------------------------------------------
def _hvac_push(button):
    """Send HVAC command to ESPHome controller via HA switch entities."""
    actions = ESPHOME_MIRROR_MAP.get(button)
    if actions is None:
        log.error(f"keenect: unknown HVAC button {button}")
        return False
    failed = []
    for action, entity in actions:
        # L9: check switch availability before issuing command — unavailable switch
        # must count as a failure so the push_fail_count / auto-disable logic engages.
        sw_state = state.get(entity)
        if sw_state in ("unavailable", "unknown", None):
            log.error(f"keenect: ESPHome {entity} offline (state={sw_state}), treating as push failure")
            failed.append(entity)
            continue
        try:
            if action == "turn_on":
                switch.turn_on(entity_id=entity)
            else:
                switch.turn_off(entity_id=entity)
            log.info(f"keenect: ESPHome {action} {entity}")
        except Exception as e:
            log.error(f"keenect: ESPHome {action} {entity} FAILED: {e}")
            failed.append(entity)
    if failed:
        _st["push_fail_count"] = _st.get("push_fail_count", 0) + 1
        fc = _st["push_fail_count"]
        log.error(f"keenect: ESPHome push failed for {failed} (fail count: {fc})")
        if fc >= 5:
            try:
                persistent_notification.create(
                    title="Keenect: ESPHome Push CRITICAL",
                    message=f"{fc} consecutive HVAC push commands failed. Keenect disabled. Check ESP32 hvac-controller.local.",
                    notification_id="keenect_push_fail")
            except Exception:
                pass
            try:
                input_boolean.turn_off(entity_id="input_boolean.keenect_enabled")
            except Exception as e2:
                log.error(f"keenect: failed to disable keenect: {e2}")
        elif fc >= 3:
            try:
                persistent_notification.create(
                    title="Keenect: ESPHome Push Failures",
                    message=f"{fc} consecutive HVAC push commands failed. Check ESP32 hvac-controller.local.",
                    notification_id="keenect_push_fail")
            except Exception:
                pass
        return False
    _st["push_fail_count"] = 0
    return True


def _hvac_esphome_mirror(button):
    """No-op: ESPHome is now the primary control path (cutover from Flask)."""
    pass


def _outdoor_temp():
    val = _float(OUTDOOR_TEMP_ENTITY)
    if val is not None:
        prev = _st.get("outdoor_temp_last_value")
        if prev is None or abs(val - prev) >= 0.1:
            _st["outdoor_temp_last_value"] = val
            _st["outdoor_temp_last_changed"] = time_mod.time()
    return val


def _hvac_turn_on():
    """Activate HVAC in current mode."""
    mode = _hvac_mode()
    if mode == "OFF":
        log.info("keenect: HVAC mode OFF, ignoring on request")
        return

    # Short-cycle protection: don't restart too soon after shutoff
    off_elapsed = time_mod.time() - _st["last_hvac_off_time"]
    sc_min = SHORT_CYCLE_COOL_MIN if mode == "COOL" else SHORT_CYCLE_HEAT_MIN
    if _st["last_hvac_off_time"] > 0 and off_elapsed < sc_min:
        log.warning(
            f"keenect: short-cycle blocked - only {off_elapsed:.0f}s since last off "
            f"(minimum {sc_min}s for {mode})"
        )
        return

    if mode == "COOL":
        ot = _outdoor_temp()
        lockout = _cool_lockout_temp()
        if ot is None:
            pass  # unavailable already fails open — allow cooling
        elif ot < -20 or ot > 130:
            log.warning(
                f"keenect: cool-lockout SKIPPED - outdoor temp {ot}°F is implausible "
                f"(< -20 or > 130), treating as unknown"
            )
        else:
            # Check staleness: if temp hasn't changed in > 45 min, treat as stale/frozen
            last_changed = _st.get("outdoor_temp_last_changed", 0.0)
            age = time_mod.time() - last_changed if last_changed else None
            if age is not None and age > 2700:  # 45 * 60 = 2700s
                log.warning(
                    f"keenect: cool-lockout SKIPPED - outdoor temp {ot}°F unchanged for "
                    f"{age/60:.0f}min (stale/frozen sensor), treating as unknown"
                )
            elif ot < lockout:
                log.warning(f"keenect: COOL blocked - outdoor temp {ot}°F < {lockout}°F")
                return
    # If already on in a different mode, turn off first
    if _st["hvac_on"] and (
        (mode == "HEAT" and _st["main_state"] == "COOLING") or
        (mode == "COOL" and _st["main_state"] == "HEATING")
    ):
        log.info(f"keenect: mode mismatch ({_st['main_state']} -> {mode}), turning off first")
        _hvac_turn_off()

    # Cancel any pending vent closure
    _st["hvac_off_time"] = None
    _st["vents_closed_after_off"] = True

    # NIT-1: flip hvac_on True before the relay pushes below, each of which is
    # an await point (switch.turn_on/off) where a concurrent trigger (e.g.
    # on_electricity_price_change -> _reconcile_peak_lockout, or
    # on_circ_change) can interleave. Those triggers check `not
    # _st["hvac_on"]` before pushing fanOff/closing vents; if hvac_on were
    # still False while heatOn/coolOn had already been pushed, they'd race in
    # and kill the fan/vents under active heat/cool. Flipping it first makes
    # them see hvac_on=True and no-op instead. All prior branches in this
    # function (short-cycle, cool-lockout, mode-mismatch at line ~872) read
    # _st["hvac_on"] before this point, so they still see the pre-call value.
    # Reverted below on push failure so a failed turn-on doesn't leave
    # hvac_on=True with the relays not reliably engaged.
    _st["hvac_on"] = True

    if mode == "HEAT":
        ok1 = _hvac_push(2)        # heatOn
        ok2 = _hvac_push(6)        # fanOn
        if not ok1 or not ok2:
            log.error("keenect: HVAC HEAT on push failed, not updating state")
            _st["hvac_on"] = False
            return
        _st["main_state"] = "HEATING"
    elif mode == "COOL":
        ok1 = _hvac_push(4)        # coolOn
        ok2 = _hvac_push(6)        # fanOn
        if not ok1 or not ok2:
            log.error("keenect: HVAC COOL on push failed, not updating state")
            _st["hvac_on"] = False
            return
        _st["main_state"] = "COOLING"
    else:
        _st["hvac_on"] = False
        return
    _st["last_hvac_on_time"] = time_mod.time()
    _st["warmup_start"] = time_mod.time()
    log.info(f"keenect: HVAC ON in {mode} mode (warmup started)")


def _hvac_turn_off(emergency=False):
    """Shut down HVAC with proper sequence.
    emergency=True bypasses min-run check (used for sensor failure shutoff)."""
    # Min-run protection: don't turn off too quickly after turning on
    if not emergency and _st["last_hvac_on_time"] > 0:
        run_elapsed = time_mod.time() - _st["last_hvac_on_time"]
        mode = _hvac_mode()
        if run_elapsed < MIN_RUN_TIME and mode != "OFF":
            log.warning(
                f"keenect: min-run blocked - only {run_elapsed:.0f}s of {MIN_RUN_TIME}s minimum"
            )
            return

    log.info(f"keenect: HVAC shutdown{' (EMERGENCY)' if emergency else ''}")
    ms = _st["main_state"]
    if ms == "HEATING":
        ok = _hvac_push(3)   # heatOff
    elif ms == "COOLING":
        ok = _hvac_push(5)   # coolOff
    else:
        ok = _hvac_push(1)   # general off

    if not ok:
        log.error("keenect: HVAC mode-off push failed, not changing state")
        return

    if _st["recirc_active"] or _circ_enabled():
        if not _hvac_push(6):   # keep fan
            log.warning("keenect: fan-on push failed during shutdown (non-critical)")
        log.info("keenect: keeping fan on (recirc/circ)")
    else:
        if not _hvac_push(7):   # fan off
            log.warning("keenect: fan-off push failed during shutdown (non-critical)")

    _st["main_state"] = "IDLE"
    _st["hvac_on"] = False
    _st["warmup_start"] = None
    _st["last_hvac_off_time"] = time_mod.time()

    # Schedule delayed vent closure (checked in periodic eval)
    # Skip if recirculation or continuous circulation is active (vents should stay open)
    if not _st["recirc_active"] and not _circ_enabled():
        _st["hvac_off_time"] = time_mod.time()
        _st["vents_closed_after_off"] = False
        delay = _vent_delay()
        log.info(f"keenect: vent closure in {delay}s (timer-based)")


# ---------------------------------------------------------------------------
# Supply air warmup check
# ---------------------------------------------------------------------------
def _is_warming_up():
    """Check if HVAC is still warming up (supply air not yet conditioned).

    Uses supply/return duct temp delta from ESPHome sensors when available,
    falls back to fixed 120s delay if sensors are offline.
    """
    if not _st["hvac_on"]:
        return False
    start = _st.get("warmup_start")
    if start is None:
        return False

    elapsed = time_mod.time() - start

    # Check timeout first (always works, even if sensors are missing)
    if elapsed >= WARMUP_FIXED_DELAY:
        _st["warmup_start"] = None
        log.info(f"keenect: warmup done (timeout, {elapsed:.0f}s)")
        return False

    # Sensor-based check: supply air is conditioned
    supply = _float(SUPPLY_TEMP_ENTITY)
    ret = _float(RETURN_TEMP_ENTITY)
    if supply is not None and ret is not None:
        _st["_warmup_sensors_ok"] = True
        mode = _hvac_mode()
        if mode == "HEAT" and supply >= ret + WARMUP_HEAT_DELTA:
            _st["warmup_start"] = None
            log.info(f"keenect: warmup done - supply {supply:.1f}°F >= return {ret:.1f}°F + {WARMUP_HEAT_DELTA} ({elapsed:.0f}s)")
            return False
        if mode == "COOL" and supply <= ret - WARMUP_COOL_DELTA:
            _st["warmup_start"] = None
            log.info(f"keenect: warmup done - supply {supply:.1f}°F <= return {ret:.1f}°F - {WARMUP_COOL_DELTA} ({elapsed:.0f}s)")
            return False
    else:
        # P8: Warmup sensor staleness — log once when sensors go missing
        if _st.get("_warmup_sensors_ok", True):
            log.warning(
                f"keenect: warmup sensors unavailable - supply={supply} return={ret}, "
                f"falling back to {WARMUP_FIXED_DELAY}s timeout"
            )
            _st["_warmup_sensors_ok"] = False

    return True


# ---------------------------------------------------------------------------
# Vent control
# ---------------------------------------------------------------------------
def _set_vent(zone_name, level):
    """Set vent opening for a zone (0-100)."""
    zone = ZONES[zone_name]
    vtype = zone.get("vent_type", "light")
    mode = _hvac_mode()
    if mode == "COOL":
        max_level = zone.get("cool_max_vo", zone.get("heat_max_vo", 100))
    else:
        max_level = zone.get("heat_max_vo", 100)
    level = max(0, min(max_level, int(level)))

    for vent_id in zone["vents"]:
        key = f"{zone_name}:{vent_id}"
        current = _st["vent_levels"].get(key, -99)
        # Dedupe both directions: skip a re-send when already within tolerance
        # of a nonzero target, OR already exactly closed. current defaults to
        # -99 when unknown, so a first-ever call still sends either way.
        # _verify_vents()/_check_vent_health() correct real drift despite this
        # cache: _verify_vents() deletes the cache entry before re-calling
        # _set_vent(), and _check_vent_health() re-sends via light.turn_on/off
        # directly, bypassing _set_vent() (and this cache) entirely.
        if level > 0 and abs(current - level) <= 4:
            continue
        if level == 0 and current == 0:
            continue

        try:
            if vtype == "number":
                # ESPHome servo via native HA number entity
                number.set_value(entity_id=vent_id, value=level)
            elif vtype == "light":
                # Keen vents via Hubitat (light entities)
                if level == 0:
                    light.turn_off(entity_id=vent_id)
                else:
                    light.turn_on(entity_id=vent_id, brightness_pct=level)
            _st["vent_levels"][key] = level
            log.info(f"keenect: {zone_name} vent {vent_id} -> {level}")
        except Exception as e:
            log.error(f"keenect: failed {vent_id} -> {level}: {e}")


def _close_zone(zone_name):
    _set_vent(zone_name, 0)


def _close_all_vents():
    for zn in ZONES:
        _close_zone(zn)


def _verify_vents():
    """Check vents actually reached their target; clear cache to retry if not."""
    for zn, zone in ZONES.items():
        vtype = zone.get("vent_type", "light")
        for vent_id in zone["vents"]:
            key = f"{zn}:{vent_id}"
            target = _st["vent_levels"].get(key)
            if target is None:
                continue
            try:
                if vtype == "light":
                    s = state.get(vent_id)
                    if target == 0 and s == "off":
                        continue  # correct
                    if target > 0 and s == "on":
                        bri = state.get(f"{vent_id}.brightness")
                        if bri is None:
                            attrs = state.getattr(vent_id)
                            bri = attrs.get("brightness", 0) if attrs else 0
                        bri = int(bri)
                        actual_pct = round(bri * 100 / 255) if bri else 0
                        if abs(actual_pct - target) <= 10:
                            continue  # close enough
                    # Mismatch - clear cache so next eval retries
                    log.warning(f"keenect: vent {vent_id} target={target} actual={s}, clearing cache to retry")
                    del _st["vent_levels"][key]
                    if target == 0 and s != "off":
                        log.warning(f"keenect: vent {vent_id} reconnected open during IDLE, forcing close")
                        _set_vent(zn, 0)
                elif vtype == "number":
                    # Servo (ESPHome number entity) — re-assert if diverged (catches ESP reboot reset)
                    s = state.get(vent_id)
                    if s in ("unavailable", "unknown", None):
                        continue  # don't crash on unavailable servo
                    try:
                        actual = float(s)
                    except (ValueError, TypeError):
                        continue
                    if abs(actual - target) > 2:  # tolerance: 2 degrees
                        log.warning(
                            f"keenect: servo {vent_id} target={target} actual={actual:.1f} "
                            f"(diverged >2°, clearing cache to re-send)"
                        )
                        del _st["vent_levels"][key]
                        _set_vent(zn, target)
            except Exception as e:
                log.debug(f"keenect: verify vent {vent_id}: {e}")


# ---------------------------------------------------------------------------
# Vent opening calculation
# ---------------------------------------------------------------------------
def _calc_opening(zone_name, zstate, temp, setpoint):
    """Vent opening: full max_vo while zone demands, fan_vo for FAN ONLY, 0 otherwise.
    No proportional taper — a demanding zone holds max until hysteresis threshold is met."""
    zone = ZONES[zone_name]
    if zstate == "HEATING":
        return zone["heat_max_vo"]
    elif zstate == "COOLING":
        return zone["cool_max_vo"]
    elif zstate == "FAN ONLY":
        return zone["fan_vo"]
    else:
        return 0


# ---------------------------------------------------------------------------
# Zone evaluation
# ---------------------------------------------------------------------------
def _get_climate_attr(entity_id, attr, default=None):
    """Read a climate entity attribute. Tries multiple pyscript access methods."""
    val = None
    try:
        # Method 1: pyscript direct attribute access
        val = state.get(f"{entity_id}.{attr}")
    except Exception:
        pass
    if val is None:
        try:
            # Method 2: getattr dict access (fallback for climate_template)
            attrs = state.getattr(entity_id)
            if attrs:
                val = attrs.get(attr)
        except Exception:
            pass
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return val  # return string values (e.g. hvac_action) as-is


def _eval_zone(zone_name):
    """Compute new zone state. Returns dict with result, or None if temp unavailable.
    Pure computation — no vent side effects."""
    zone = ZONES[zone_name]
    tstat = zone["thermostat"]

    # Read raw sensor for precise temp (avoids climate_template temp_step rounding)
    temp = _float(zone["temp_sensor"])
    # Setpoints from climate entity attributes
    heat_sp = _get_climate_attr(tstat, "temperature")  # target temp (single-setpoint)
    cool_sp = _get_climate_attr(tstat, "target_temp_high")  # cool setpoint (heat_cool mode)
    target_low = _get_climate_attr(tstat, "target_temp_low")
    if target_low is not None:
        heat_sp = target_low
    if cool_sp is None and _hvac_mode() == "COOL":
        cool_sp = heat_sp

    if temp is None:
        return None

    heat_sp = float(heat_sp) if heat_sp is not None else None
    cool_sp = float(cool_sp) if cool_sp is not None else None

    # Away mode: override setpoints with away values (wider deadband)
    away_entity = f"input_boolean.away_{zone_name}"
    if state.get(away_entity) == "on":
        away_heat = _float("input_number.away_heat_setpoint", 55.0)
        away_cool = _float("input_number.away_cool_setpoint", 85.0)
        if heat_sp is not None:
            heat_sp = min(heat_sp, away_heat)
        if cool_sp is not None:
            cool_sp = max(cool_sp, away_cool)

    # Thermostat's hvac_action is the stateless start signal (temp < setpoint).
    # In away mode, thermostat still uses normal setpoints, so we evaluate demand ourselves.
    is_away = state.get(away_entity) == "on"
    op_raw = _get_climate_attr(tstat, "hvac_action")
    tstat_action = (str(op_raw) if op_raw else "idle").upper()

    old = _st["zone_states"].get(zone_name, "IDLE")
    mode = _hvac_mode()
    hyst = _hysteresis()

    # Hybrid hysteresis:
    #   START heating: temp < setpoint (away: pyscript evaluates; normal: thermostat says "heating")
    #   STOP heating:  pyscript keeps heating until temp >= setpoint + hysteresis
    #   START cooling: temp > setpoint (away: pyscript evaluates; normal: thermostat says "cooling")
    #   STOP cooling:  pyscript keeps cooling until temp <= setpoint - hysteresis
    op = "IDLE"
    if mode == "HEAT" and heat_sp is not None:
        if old == "HEATING":
            op = "IDLE" if temp >= heat_sp + hyst else "HEATING"
        elif is_away and temp < heat_sp:
            op = "HEATING"
        elif tstat_action == "HEATING":
            op = "HEATING"
    elif mode == "COOL" and cool_sp is not None:
        if old == "COOLING":
            op = "IDLE" if temp <= cool_sp - hyst else "COOLING"
        elif is_away and temp > cool_sp:
            op = "COOLING"
        elif tstat_action == "COOLING":
            op = "COOLING"
        elif op_raw is None and not is_away and temp > cool_sp + hyst:
            # L5: climate_template hvac_action unavailable — raw-sensor fallback for COOL START.
            # Uses setpoint + hysteresis (conservative) to avoid false triggers.
            op = "COOLING"
            log.warning(
                f"keenect: {zone_name} COOL START via raw-sensor fallback "
                f"(hvac_action unavailable, temp={temp:.1f} > {cool_sp + hyst:.1f})"
            )

    return {"op": op, "old": old, "temp": temp, "heat_sp": heat_sp, "cool_sp": cool_sp, "hyst": hyst}


def _apply_zone_vents(results):
    """Apply vent actions using complete zone state picture (order-independent).
    Called after all zones have been evaluated."""
    warming_up = _is_warming_up()
    for zone_name, r in results.items():
        op = r["op"]
        old = r["old"]
        temp = r["temp"]
        hyst = r["hyst"]

        # Update stored state
        _st["zone_states"][zone_name] = op

        if op in ("HEATING", "COOLING", "FAN ONLY"):
            # Use effective stop point as target so vent stays open through overshoot region
            if op == "HEATING":
                target = r["heat_sp"] + hyst
            elif op == "COOLING":
                target = r["cool_sp"] - hyst
            else:
                target = r["heat_sp"]
            opening = _calc_opening(zone_name, op, temp, target)
            # Heat mode only: cap vents at minimum during warmup to avoid pushing
            # cold air through ducts before the heat exchanger is up to temp.
            # Cool mode does not need this: AC coil cools within seconds of compressor start.
            if warming_up and op == "HEATING":
                zone = ZONES[zone_name]
                if opening > zone["heat_min_vo"]:
                    opening = zone["heat_min_vo"]
            _set_vent(zone_name, opening)
        elif op == "IDLE" and old != "IDLE":
            # Zone just went idle — check others using the COMPLETE new state picture
            others_active = any([
                results[n]["op"] not in ("IDLE", "OFF", "")
                for n in results if n != zone_name
            ])
            if others_active:
                log.info(f"keenect: {zone_name} idle, others active -> close vents")
                _close_zone(zone_name)
            else:
                log.info(f"keenect: {zone_name} idle (last zone), delayed closure")

        if op != old:
            log.info(
                f"keenect: {zone_name} {old}->{op} temp={temp} "
                f"hsp={r['heat_sp']} csp={r['cool_sp']} hyst={hyst}"
            )


def _all_idle():
    return all([s in ("IDLE", "OFF", "") for s in _st["zone_states"].values()])


# ---------------------------------------------------------------------------
# Cost tracking — Lennox G61MPV two-stage furnace
# ---------------------------------------------------------------------------
# Input BTU/h: high=88000, low=60000. Default to low fire (most common).
# Cost = input_BTU/h × hours / 100,000 BTU/therm × $/therm
FURNACE_BTU_DEFAULT = 82400  # G61MPV ~80% high fire average

def _track_cost():
    """Accumulate runtime and cost each eval cycle when HVAC is active."""
    now = time_mod.time()
    last = _st.get("last_cost_time")
    _st["last_cost_time"] = now
    if last is None:
        return  # first call after startup, no elapsed time to accumulate
    elapsed_h = (now - last) / 3600.0
    if elapsed_h <= 0 or elapsed_h > 0.1:  # sanity: skip if >6 min gap (restart)
        return
    if not _st["hvac_on"]:
        return
    mode = _hvac_mode()
    if mode == "HEAT":
        btu = _float("input_number.furnace_btu_input", FURNACE_BTU_DEFAULT)
        price = _float("input_number.gas_rate_per_therm", 1.00)
        cost = (btu * elapsed_h / 100000.0) * price
        _st["heat_runtime"] += elapsed_h
        _st["heat_cost"] += cost
    elif mode == "COOL":
        _st["cool_runtime"] += elapsed_h
    _update_cost_sensors()


def _update_cost_sensors():
    """Publish cost/runtime as HA sensors with total_increasing for utility_meter."""
    state.set("sensor.hvac_heat_runtime", round(_st["heat_runtime"], 4), {
        "unit_of_measurement": "h",
        "state_class": "total_increasing",
        "device_class": "duration",
        "icon": "mdi:fire",
        "friendly_name": "Heating Runtime",
    })
    state.set("sensor.hvac_heat_cost", round(_st["heat_cost"], 2), {
        "unit_of_measurement": "$",
        "state_class": "total_increasing",
        "icon": "mdi:currency-usd",
        "friendly_name": "Heating Cost",
    })
    state.set("sensor.hvac_cool_runtime", round(_st["cool_runtime"], 4), {
        "unit_of_measurement": "h",
        "state_class": "total_increasing",
        "device_class": "duration",
        "icon": "mdi:snowflake",
        "friendly_name": "Cooling Runtime",
    })


# ---------------------------------------------------------------------------
# ETA to target
# ---------------------------------------------------------------------------
def _update_eta_sensors(results):
    """Compute per-zone ETA-to-target from rolling in-memory temp buffer.
    Updates sensor.keenect_eta_{zone} for each zone in results.
    Uses least-squares linear slope over last 10 min of temp readings.
    Pyscript sandbox: no generator expressions — list comprehensions only."""
    now = time_mod.time()
    history_window = 600  # 10 minutes
    hyst = _hysteresis()  # zones run to setpoint -/+ hyst (see _eval_zone)

    if "zone_temp_history" not in _st:
        _st["zone_temp_history"] = {}

    for zone_name, r in results.items():
        op = r["op"]
        temp = r["temp"]
        heat_sp = r["heat_sp"]
        cool_sp = r["cool_sp"]

        # Append current reading; trim to window (list comprehension)
        buf = _st["zone_temp_history"].get(zone_name, [])
        buf.append((now, temp))
        cutoff = now - history_window
        buf = [pt for pt in buf if pt[0] >= cutoff]
        _st["zone_temp_history"][zone_name] = buf

        # Determine active setpoint and direction
        eta_state = "—"
        rate_f_per_min = None
        setpoint_used = None

        if op == "HEATING" and heat_sp is not None:
            setpoint_used = heat_sp
            # ETA targets the actual stop point (heat_sp + hyst), not the bare
            # setpoint — the zone keeps heating through the hysteresis band.
            gap = (heat_sp + hyst) - temp     # positive until it stops
        elif op == "COOLING" and cool_sp is not None:
            setpoint_used = cool_sp
            gap = temp - (cool_sp - hyst)     # positive until it stops
        else:
            gap = None

        # Compute least-squares linear slope once per zone for reuse below.
        slope = None
        if len(buf) >= 3:
            t0 = buf[0][0]
            n = len(buf)
            xs = [(pt[0] - t0) / 60.0 for pt in buf]
            ys = [pt[1] for pt in buf]
            sum_x = sum(xs)
            sum_y = sum(ys)
            sum_xx = sum([x * x for x in xs])
            sum_xy = sum([x * y for x, y in zip(xs, ys)])
            denom = n * sum_xx - sum_x * sum_x
            if abs(denom) > 1e-9:
                slope = (n * sum_xy - sum_x * sum_y) / denom
                rate_f_per_min = round(slope, 4)

        # ETA to target (HEATING / COOLING zones)
        if gap is not None and gap > 0:
            if slope is not None:
                if op == "HEATING" and slope > 0.001:
                    eta_min = gap / slope
                    eta_state = ">120" if eta_min > 120 else str(round(eta_min))
                elif op == "COOLING" and slope < -0.001:
                    eta_min = gap / abs(slope)
                    eta_state = ">120" if eta_min > 120 else str(round(eta_min))
                else:
                    eta_state = "calculating…"
            else:
                eta_state = "calculating…"

        # ETA to start (IDLE zones): how long until temp drifts to the demand threshold?
        eta_start_state = "—"
        setpoint_start = None
        if op == "IDLE":
            sys_mode = _hvac_mode()
            # "calculating…" = not enough data yet (no slope); "stable" = we have
            # a trend but temp is flat or drifting AWAY from the threshold, so no
            # cycle is approaching (common at idle when HVAC is holding the house).
            if sys_mode == "HEAT" and heat_sp is not None:
                setpoint_start = heat_sp
                gap_start = temp - heat_sp  # positive when above setpoint
                if gap_start <= 0:
                    eta_start_state = "—"
                elif slope is None:
                    eta_start_state = "calculating…"
                elif slope < -0.001:  # cooling toward the heat threshold
                    eta_min = gap_start / abs(slope)
                    eta_start_state = ">120" if eta_min > 120 else str(round(eta_min))
                else:
                    eta_start_state = "stable"
            elif sys_mode == "COOL" and cool_sp is not None:
                setpoint_start = cool_sp
                gap_start = cool_sp - temp  # positive when below setpoint
                if gap_start <= 0:
                    eta_start_state = "—"
                elif slope is None:
                    eta_start_state = "calculating…"
                elif slope > 0.001:  # warming toward the cool threshold
                    eta_min = gap_start / slope
                    eta_start_state = ">120" if eta_min > 120 else str(round(eta_min))
                else:
                    eta_start_state = "stable"

        display = zone_name.replace("_", " ").title()
        state.set(f"sensor.keenect_eta_{zone_name}", eta_state, {
            "friendly_name": f"{display} ETA to Target",
            "icon": "mdi:timer-outline",
            "unit_of_measurement": "min",
            "current_temp": temp,
            "setpoint": setpoint_used,
            "rate_f_per_min": rate_f_per_min,
            "zone": zone_name,
            "mode": op,
        })
        state.set(f"sensor.keenect_eta_start_{zone_name}", eta_start_state, {
            "friendly_name": f"{display} ETA to Start",
            "icon": "mdi:timer-sand",
            "unit_of_measurement": "min",
            "current_temp": temp,
            "setpoint": setpoint_start,
            "rate_f_per_min": rate_f_per_min,
            "zone": zone_name,
            "mode": op,
        })


# ---------------------------------------------------------------------------
# Master evaluation
# ---------------------------------------------------------------------------
def _eval_followup():
    """Replay a debounced _eval_master() call once its debounce window clears.

    Spawned via task.create() from _eval_master()'s debounce-skip branch, so
    a setpoint edit (or any other trigger) that arrives while another eval is
    already inside its 2s debounce window still gets evaluated promptly,
    instead of being silently dropped until the next 15s periodic_eval tick
    (2.6.7's gap — measured live: 14.9s to COOLING instead of the intended
    <100ms).

    task.unique() collapses bursts: if several debounced calls happen close
    together (e.g. a slider drag emitting many input_number changes in a
    couple of seconds), each spawns its own _eval_followup() task, but the
    first line here cancels any still-pending earlier instance, so only the
    most-recently-spawned one survives to actually run. Net effect: N
    debounced calls in a burst -> exactly one follow-up eval, not N.

    The wait loop re-reads debounce_until each time it wakes, because a real
    (non-debounced) _eval_master() call can run and push debounce_until
    further out while this task is sleeping — waiting once on a stale value
    could call into an active debounce window and get silently dropped again.
    Once the loop exits, debounce_until is in the past, so the _eval_master()
    call below passes the gate the normal way — same re-entrancy guarantee,
    no concurrent execution.
    """
    task.unique("keenect_eval_followup")
    try:
        while True:
            wait = _st["debounce_until"] - time_mod.time()
            if wait <= 0:
                break
            task.sleep(wait + 0.05)
        _eval_master()
    except Exception as e:
        log.error(f"keenect: eval follow-up crashed: {e}")


def _enforce_away_setpoints():
    """Continuously reclamp setpoints for any zone in away mode.

    2.6.7-2.6.8 only forced away setpoints at startup (on_startup) and at the
    away on/off transition (on_away_change). In between, nothing stopped a
    manual setpoint change from sticking for the rest of the away period —
    confirmed live 2026-07-31: away_gene held setpoint=79 for 17 minutes
    (should have been 78) until an unrelated pyscript.reload happened to run
    on_startup's one-shot check. Away mode is meant to be authoritative, so
    this now runs every _eval_master() pass (called right after the debounce
    gate below, before any zone is evaluated) and reasserts the away value
    the same way on_startup already does, just on every pass instead of once.

    Per-setpoint equality check (not a combined heat+cool check like
    on_startup's) so a zone that's already correct stays silent — no write,
    no log line — every 15s in steady state, per the "only act on a real
    difference" requirement.

    Feedback-loop note: writing input_number.<zone>_{heat,cool}_setpoint
    fires on_setpoint_change, which calls _eval_master() immediately. That
    re-entrant call always lands inside the debounce window this function's
    caller just opened (debounce_until = now + 2, set before this function
    runs), so it can't recurse into this function again — it just spawns
    _eval_followup() (task.unique-collapsed, so a burst still yields exactly
    one follow-up). By the time that follow-up eval runs, the write already
    landed, so this function finds the setpoint already correct and no-ops.
    Self-terminating, no new locking needed.

    Attribution (FIX A, 2.6.10): this function does NOT touch
    setpoint_user_pending — 2.6.9 stashed "Away" there right before each
    write, reusing the same per-zone slot on_climate_set_temperature uses for
    real users, and that was racy: on_setpoint_change calls _eval_master()
    (which runs this function) BEFORE resolving its own attribution, so a
    real user's stash could be clobbered by this function's "Away" stash
    before the user's own on_setpoint_change call ever read it — confirmed
    live 2026-07-31 17:21 as an inverted mislog (manual change logged "Away",
    the reclamp that corrected it logged "System"). Attribution is now
    resolved entirely in on_setpoint_change() instead: a still-"System"
    result gets relabeled "Away" there when the zone is in away mode and the
    new value exactly equals the away setpoint — deterministic, independent
    of write ordering. See that function's docstring for the full mechanism.
    """
    away_heat = _float("input_number.away_heat_setpoint", 55.0)
    away_cool = _float("input_number.away_cool_setpoint", 85.0)
    for zn in ZONES:
        if state.get(f"input_boolean.away_{zn}") != "on":
            continue
        heat_key = f"input_number.{zn}_heat_setpoint"
        cool_key = f"input_number.{zn}_cool_setpoint"
        cur_heat = _float(heat_key)
        cur_cool = _float(cool_key)
        if cur_heat is not None and cur_heat != away_heat:
            input_number.set_value(entity_id=heat_key, value=away_heat)
            log.info(f"keenect: {zn} away reclamp - heat {cur_heat} -> {away_heat}")
        if cur_cool is not None and cur_cool != away_cool:
            input_number.set_value(entity_id=cool_key, value=away_cool)
            log.info(f"keenect: {zn} away reclamp - cool {cur_cool} -> {away_cool}")


def _eval_master():
    if not _enabled():
        _update_status()
        return

    if state.get("switch.hvac_controller_bypass") == "on":
        if _st.get("hvac_on"):
            log.warning("keenect: ESP32 bypass active — ceding control, clearing COOLING/HEATING state")
            _st["hvac_on"] = False
            _st["main_state"] = "IDLE"
            _st["warmup_start"] = None
        _update_status()
        _save_if_changed()
        return

    now = time_mod.time()
    if now < _st["debounce_until"]:
        # FIX (2.6.8): a debounced call is not simply lost — spawn a
        # follow-up that replays _eval_master() once the window clears.
        # See _eval_followup() docstring.
        task.create(_eval_followup)
        return
    _st["debounce_until"] = now + 2

    # FIX (2.6.9): reclamp any away-mode zone's setpoints before evaluating
    # demand, so this pass's Pass-1 math and the displayed setpoint agree.
    # See _enforce_away_setpoints() docstring for the feedback-loop analysis.
    _enforce_away_setpoints()

    # Pass 1: compute new zone states (no vent side effects)
    results = {}
    none_zones = []
    for zn in ZONES:
        r = _eval_zone(zn)
        if r is not None:
            results[zn] = r
            # Sensor OK — reset failure counter
            _st["sensor_fail_count"][zn] = 0
        else:
            none_zones.append(zn)
            fc = _st["sensor_fail_count"].get(zn, 0) + 1
            _st["sensor_fail_count"][zn] = fc
            if fc == SENSOR_FAIL_ALERT:
                log.warning(f"keenect: sensor failure alert - {zn} returned None {fc} times")
                try:
                    persistent_notification.create(
                        title=f"Keenect: Sensor Failure ({zn})",
                        message=f"Temperature sensor for zone '{zn}' has returned None "
                                f"{fc} consecutive times (~{fc * 15}s). Check sensor.",
                        notification_id=f"keenect_sensor_fail_{zn}",
                    )
                except Exception:
                    pass
            if fc >= SENSOR_FAIL_NEUTRAL:
                zone = ZONES[zn]
                fan_vo = zone.get("fan_vo", 30)
                log.warning(f"keenect: sensor dead - {zn} None {fc}x, moving vent to neutral ({fan_vo})")
                _set_vent(zn, fan_vo)
                _st["sensor_fail_parked"] = True

    # Emergency: ALL zones returning None while HVAC is on
    if len(none_zones) == len(ZONES) and _st.get("hvac_on"):
        _st["all_none_count"] = _st.get("all_none_count", 0) + 1
        if _st["all_none_count"] < 3:
            log.warning(
                f"keenect: all-zone sensor failure #{_st['all_none_count']}/3 "
                f"(debouncing - need 3 consecutive before emergency shutoff)"
            )
        else:
            log.error(
                f"keenect: EMERGENCY - ALL zone sensors None for {_st['all_none_count']} "
                f"consecutive evals while HVAC on, forcing off"
            )
            # Direct push commands — bypass normal _hvac_turn_off checks
            _hvac_push(3)   # heatOff
            _hvac_push(5)   # coolOff
            _hvac_push(7)   # fanOff
            _st["main_state"] = "IDLE"
            _st["hvac_on"] = False
            _st["warmup_start"] = None
            # NOTE: intentionally NOT stamping last_hvac_off_time here so the
            # short-cycle guard does NOT arm on a sensor-failure-driven shutoff.
            # Cooling can resume immediately when sensors return.
            try:
                persistent_notification.create(
                    title="Keenect: EMERGENCY SHUTOFF",
                    message="ALL zone temperature sensors returned None for 3+ consecutive "
                            "evaluations. HVAC has been force-shut-off. Check sensor "
                            "connectivity immediately.",
                    notification_id="keenect_emergency_shutoff",
                )
            except Exception:
                pass
            _save_if_changed()
            return
    else:
        # At least one zone healthy — reset the debounce counter
        if _st.get("all_none_count", 0) > 0:
            _st["all_none_count"] = 0
        # L7: clear sensor_fail_parked once ALL sensors are healthy so it cannot
        # cause a spurious circulation-vent close after sensors recover.
        if not none_zones and _st.get("sensor_fail_parked"):
            _st["sensor_fail_parked"] = False
            log.info("keenect: all sensors healthy, clearing sensor_fail_parked flag")

    # Pass 2: apply vent actions with complete picture (order-independent)
    _apply_zone_vents(results)
    _update_eta_sensors(results)

    demanding = sum([1 for r in results.values() if r["op"] in ("HEATING", "COOLING")])
    mode = _hvac_mode()

    # Stale-vent sweep: during active HVAC with at least one demanding zone, close any
    # idle zone whose vent is still open.  _apply_zone_vents only fires the idle-close
    # path on transition (old != "IDLE"), so zones that were already IDLE when HVAC
    # activated are never closed there.  This sweep is idempotent — _close_zone is a
    # no-op when the vent is already 0.
    if _st["hvac_on"] and demanding > 0:
        # vent_levels keys are "zone:vent_id", so match by zone prefix (a bare
        # zone-name .get() always missed — the sweep was a silent no-op).
        stale = [z for z, zst in _st["zone_states"].items()
                 if zst in ("IDLE", "OFF", "")
                 and len([1 for k, v in _st["vent_levels"].items()
                          if k.startswith(z + ":") and v > 0]) > 0]
        if stale:
            log.info(f"keenect: stale-vent sweep - closing idle zones with open vents: {stale}")
            for z in stale:
                _close_zone(z)

    if demanding > 0:
        if mode == "OFF":
            log.info(f"keenect: {demanding} zones demanding but mode OFF")
            return
        if _st["recirc_active"]:
            _stop_recirc("Zone demand")
        if not _st["hvac_on"]:
            log.info(f"keenect: activating HVAC for {demanding} zones")
            _hvac_turn_on()
    else:
        if _st["hvac_on"] and not _st["recirc_active"]:
            log.info("keenect: all idle, shutting HVAC off")
            _hvac_turn_off()
        elif not _st["hvac_on"] and not _st["recirc_active"] and not _circ_enabled():
            # Idle reconciliation: if HVAC is off and no circ/recirc, ensure vents are
            # closed. This self-heals states where vents were left open by sensor-failure
            # neutral positioning (SENSOR_FAIL_NEUTRAL) after sensors recovered.
            open_vents = [k for k, v in _st["vent_levels"].items() if v > 0]
            if open_vents:
                log.info(f"keenect: idle reconciliation - closing vents left open: {open_vents}")
                _close_all_vents()
                _st["vents_closed_after_off"] = True
        elif (not _st["hvac_on"] and not _st["recirc_active"]
              and _st.get("sensor_fail_parked") and not none_zones):
            # Sensor-fail recovery: vents were parked at fan_vo due to sensor failure.
            # Sensors are now healthy (none_zones empty). Close vents and clear the flag,
            # bypassing the _circ_enabled() gate — circ will reopen them if needed, but
            # we must not leave parked vents stranded just because circ is enabled.
            open_vents = [k for k, v in _st["vent_levels"].items() if v > 0]
            log.info(f"keenect: sensor-fail recovery - sensors healthy, closing parked vents: {open_vents}")
            _close_all_vents()
            _st["sensor_fail_parked"] = False
            _st["vents_closed_after_off"] = True
        elif (not _st["hvac_on"] and not _st["recirc_active"] and _circ_enabled()
              and _relays_confirmed_off()):
            # Circ reconciliation: ensure non-excluded zones are at fan_vo, excluded at 0,
            # while circulation is active and the system is idle. Self-heals the case where
            # circ was enabled during an HVAC run (deferred) and HVAC then ended.
            # _relays_confirmed_off() (not just hvac_on) gates this: right after a HA
            # restart hvac_on can be False merely because the ESPHome switches hadn't
            # reported in yet when startup reconciled it (see on_startup), not because
            # the relay is actually off. This elif runs every 15s via periodic_eval, so
            # once the switches report a definite "off" the very next pass opens
            # circulation normally — no separate re-check mechanism needed.
            for zn, zone in ZONES.items():
                if _zone_circ_excluded(zn):
                    _set_vent(zn, 0)
                else:
                    _set_vent(zn, zone.get("fan_vo", 30))

    # Check delayed vent closure timer
    _check_vent_closure_timer(now)

    _track_cost()
    _update_status()
    _save_if_changed()


def _check_vent_closure_timer(now):
    """Close vents after HVAC off delay has elapsed."""
    if _st["vents_closed_after_off"]:
        return
    if _st["hvac_off_time"] is None:
        return
    if _st["hvac_on"]:
        # HVAC came back on - cancel closure
        _st["hvac_off_time"] = None
        _st["vents_closed_after_off"] = True
        return
    if _st["recirc_active"] or _circ_enabled():
        return

    elapsed = now - _st["hvac_off_time"]
    delay = _vent_delay()
    if elapsed >= delay:
        log.info(f"keenect: closing all vents ({elapsed:.0f}s since HVAC off)")
        _close_all_vents()
        _st["vents_closed_after_off"] = True
        _st["hvac_off_time"] = None


# ---------------------------------------------------------------------------
# Recirculation
# ---------------------------------------------------------------------------
def _check_recirc():
    if not _recirc_enabled() or not _enabled():
        return

    if _peak_lockout():
        # Never start (or continue) smart recirculation during an on-peak
        # window. Zone demand always wins over this — _stop_recirc only
        # touches the fan/vents, it never gates heat/cool.
        if _st["recirc_active"]:
            _stop_recirc("On-peak lockout")
        _st["last_all_idle"] = None
        return

    now = time_mod.time()
    delay_s = _recirc_delay_min() * 60

    if _all_idle() and not _st["hvac_on"]:
        if _st["last_all_idle"] is None:
            _st["last_all_idle"] = now
            log.info(f"keenect: recirc timer started ({_recirc_delay_min()}m)")
        elif (now - _st["last_all_idle"]) >= delay_s and not _st["recirc_active"]:
            _start_recirc()
    else:
        if _st["recirc_active"]:
            _stop_recirc("Zone demand")
        _st["last_all_idle"] = None


def _start_recirc():
    log.info("keenect: starting recirculation")
    _st["recirc_active"] = True
    for zn, zone in ZONES.items():
        if not _zone_circ_excluded(zn):
            _set_vent(zn, zone.get("fan_vo", 30))
    _hvac_push(6)  # fanOn


def _stop_recirc(reason=""):
    log.info(f"keenect: stopping recirculation ({reason})")
    _st["recirc_active"] = False
    _st["last_all_idle"] = None
    if not _circ_enabled():
        _hvac_push(7)  # fanOff
    for zn in ZONES:
        if _st["zone_states"].get(zn, "IDLE") == "IDLE":
            _close_zone(zn)


# ---------------------------------------------------------------------------
# Consistency check
# ---------------------------------------------------------------------------
def _check_consistency():
    if not _enabled():
        return
    if _all_idle() and _st["hvac_on"] and not _st["recirc_active"]:
        log.warning("keenect: consistency - all idle but HVAC on, forcing off")
        _hvac_turn_off()
        _save_if_changed()
    # Guard against stale hvac_off_time (e.g., from restored state with old timestamp)
    if _st["hvac_off_time"] is not None and not _st["vents_closed_after_off"]:
        age = time_mod.time() - _st["hvac_off_time"]
        if age > 600:  # 10 minutes - way past any reasonable delay
            log.warning(f"keenect: consistency - stale hvac_off_time ({age:.0f}s), closing vents")
            _close_all_vents()
            _st["vents_closed_after_off"] = True
            _st["hvac_off_time"] = None
            _save_if_changed()


# ---------------------------------------------------------------------------
# Status entity updates
# ---------------------------------------------------------------------------
def _update_status():
    """Publish current state as HA sensor entities for dashboard visibility."""
    # Main status
    if _st["recirc_active"]:
        status = "RECIRC"
    elif _circ_enabled() and not _st["hvac_on"]:
        status = "CIRCULATING"
    else:
        status = _st["main_state"]
    ot = _outdoor_temp()
    try:
        state.set("sensor.keenect_status", status, {
            "friendly_name": "Keenect Status",
            "icon": "mdi:hvac",
            "hvac_on": _st["hvac_on"],
            "recirc_active": _st["recirc_active"],
            "circulating": _circ_enabled(),
            "outdoor_temp": ot,
            "cool_lockout": ot is not None and ot < _cool_lockout_temp(),
            "warmup_sensor_available": _st.get("_warmup_sensors_ok", True),
        })
    except Exception as e:
        log.warning(f"keenect: status update failed: {e}")
        return

    # Per-zone vent levels
    for zn, zone in ZONES.items():
        zstate = _st["zone_states"].get(zn, "IDLE")
        vtype = zone.get("vent_type", "light")
        level = 0
        for vent_id in zone["vents"]:
            if vtype == "light":
                # Read actual brightness from light entity (0-255 scale)
                try:
                    s = state.get(vent_id)
                    if s == "on":
                        bri = state.get(f"{vent_id}.brightness")
                        if bri is None:
                            attrs = state.getattr(vent_id)
                            bri = attrs.get("brightness", 0) if attrs else 0
                        pct = round(int(bri) * 100 / 255) if bri else 0
                        level = max(level, pct)
                except Exception:
                    level = max(level, 0)
            elif vtype == "number":
                # ESPHome number entity - read actual state, map 0-45° to 0-100%
                try:
                    val = _float(vent_id, 0)
                    max_angle = zone.get("heat_max_vo", 45)
                    pct = round(val * 100 / max_angle) if max_angle > 0 else 0
                    level = max(level, min(100, pct))
                except Exception:
                    key = f"{zn}:{vent_id}"
                    raw = _st["vent_levels"].get(key, 0)
                    max_angle = zone.get("heat_max_vo", 45)
                    pct = round(raw * 100 / max_angle) if max_angle > 0 else 0
                    level = max(level, min(100, pct))

        name = f"Keenect {zn.replace('_', ' ').title()} Vent"
        unit = "%"
        icon = "mdi:air-filter"

        state.set(f"sensor.keenect_vent_{zn}", level, {
            "friendly_name": name,
            "unit_of_measurement": unit,
            "icon": icon,
            "zone_state": zstate,
        })


# ---------------------------------------------------------------------------
# Vent health check
# ---------------------------------------------------------------------------
def _round_health_value(sensor_id, raw_value):
    """Round a health-sensor reading for compact storage and change-detection.
    Pressure readings are large (~98000+, mislabeled 'mbar' but actually Pa)
    — integer resolution is plenty. Temperature needs one decimal."""
    if sensor_id.endswith("_pressure"):
        return round(raw_value)
    return round(raw_value, 1)


def _sensor_liveness_age(sensor_id, raw_value, now_ts):
    """Track (rounded_value, wallclock_when_last_changed) per sensor in
    _st["sensor_liveness"] (persisted — see _save_vent_health_state /
    _restore_vent_health_state). Returns seconds since the value last
    actually changed — 0.0 the first time a sensor is seen or whenever it
    changes.

    This replaces trusting the entity's `last_updated` attribute, which the
    Hubitat integration re-stamps to "now" on every HA restart even when the
    underlying value is a cached, days-stale reading (confirmed 2026-07-31:
    sensor.keen_mbr_2_pressure showed last_updated=17:51:47Z carrying the
    identical 07-27 value 97570.5, with no recorder history row since
    07-27 — a 4-day-old fault reported as "stale 38m"). Tracking VALUE
    CHANGE is immune to that: a restart re-stamping an unchanged value does
    not reset the age. This is sound specifically because both pressure and
    temperature drift continuously on a live sensor (verified: pressure
    ~10min cadence, temperature ~5min) — a value that truly never changes
    is itself the correct signal of a dead sensor, not a false positive.
    """
    rounded = _round_health_value(sensor_id, raw_value)
    liveness = _st.setdefault("sensor_liveness", {})
    entry = liveness.get(sensor_id)
    if entry is None or entry[0] != rounded:
        liveness[sensor_id] = (rounded, now_ts)
        return 0.0
    return now_ts - entry[1]


def _save_vent_health_state():
    """Persist per-sensor liveness to a dedicated input_text (VENT_HEALTH_ENTITY),
    kept separate from STATE_ENTITY which has almost no headroom left (233/255
    chars observed live). Compact "<code>:<value>:<ts>" triples, semicolon-
    delimited, truncated from the end if over the 255-char hard cap — losing
    the least-recently-updated entries is an acceptable degrade (they just
    re-learn freshness from the next couple of live readings)."""
    liveness = _st.get("sensor_liveness", {})
    parts = []
    for sensor_id, (val, ts) in liveness.items():
        code = _HEALTH_SENSOR_CODE.get(sensor_id)
        if not code:
            continue
        parts.append(f"{code}:{val}:{int(ts)}")
    val_str = ";".join(parts)
    while len(val_str) > 255 and ";" in val_str:
        val_str = val_str[:val_str.rfind(";")]
    try:
        input_text.set_value(entity_id=VENT_HEALTH_ENTITY, value=val_str if val_str else " ")
    except Exception as e:
        # Debug, not error/warning: this input_text helper must be created via
        # the HA WS API before it exists (see VENT_HEALTH_ENTITY comment) —
        # until then this is expected to fail every call.
        log.debug(f"keenect: failed to save vent health state (helper may not exist yet): {e}")


def _restore_vent_health_state():
    """Restore per-sensor liveness tracking after a restart (survives pyscript
    reload too, since it's read fresh from the input_text each time)."""
    try:
        raw = state.get(VENT_HEALTH_ENTITY)
        if raw in (None, "", " ", "unknown", "unavailable"):
            return
        liveness = {}
        for part in raw.split(";"):
            fields = part.split(":")
            if len(fields) != 3:
                continue
            code, val, ts = fields
            sensor_id = _HEALTH_CODE_SENSOR.get(code)
            if not sensor_id:
                continue
            try:
                liveness[sensor_id] = (float(val), float(ts))
            except ValueError:
                continue
        if liveness:
            _st["sensor_liveness"] = liveness
            log.info(f"keenect: restored vent-health liveness for {len(liveness)} sensors")
    except Exception as e:
        log.warning(f"keenect: failed to restore vent health state: {e}")


def _save_saved_setpoints_state():
    """Persist _st["saved_setpoints"] (FIX B, 2.6.10) — pre-away setpoints,
    only present for zones currently in away mode — to a dedicated input_text
    (SAVED_SETPOINTS_ENTITY), mirroring _save_vent_health_state()'s pattern:
    compact "<code>:<heat>:<cool>" triples, semicolon-delimited. Unlike the
    vent-health/setpoint-log helpers this never grows unbounded (at most one
    entry per zone, max 4 zones) — worst case is ~4x9=36 chars plus 3
    separators, nowhere near the 255-char cap, so no truncation logic is
    needed. Called only when saved_setpoints actually changes (away on/off in
    on_away_change, and the startup away-reconcile block), not on a timer —
    so the persisted value stays stable in steady state, same as the
    in-memory dict."""
    saved = _st.get("saved_setpoints", {})
    parts = []
    for zn, vals in saved.items():
        code = _ZONE_TO_CODE.get(zn.replace("_", " ").title())
        if not code:
            continue
        heat = vals.get("heat")
        cool = vals.get("cool")
        if heat is None or cool is None:
            continue
        parts.append(f"{code}:{int(heat)}:{int(cool)}")
    val_str = ";".join(parts)
    try:
        input_text.set_value(entity_id=SAVED_SETPOINTS_ENTITY, value=val_str if val_str else " ")
    except Exception as e:
        # Debug, not error/warning: this input_text helper must be created via
        # the HA WS API before it exists (see SAVED_SETPOINTS_ENTITY comment)
        # — until then this is expected to fail every call, same degrade as
        # _save_vent_health_state().
        log.debug(f"keenect: failed to save saved-setpoints state (helper may not exist yet): {e}")


def _restore_saved_setpoints_state():
    """Restore _st["saved_setpoints"] after a restart (FIX B, 2.6.10).

    Only repopulates the in-memory dict — never writes a setpoint itself. The
    restored value is only ever APPLIED at the away→off transition in
    on_away_change(), exactly like the in-memory value was already used
    before this fix. A restored entry for a zone whose away boolean is
    currently off, or that no longer exists in ZONES (deleted from config),
    is dropped silently (debug log) rather than applied — restoring is not
    supposed to re-arm away mode on its own."""
    try:
        raw = state.get(SAVED_SETPOINTS_ENTITY)
        if raw in (None, "", " ", "unknown", "unavailable"):
            return
        restored = {}
        for part in raw.split(";"):
            fields = part.split(":")
            if len(fields) != 3:
                continue
            code, heat, cool = fields
            display = _CODE_TO_ZONE.get(code)
            if not display:
                continue
            zn = display.lower().replace(" ", "_")
            if zn not in ZONES:
                log.debug(f"keenect: dropping restored saved_setpoints for unknown zone code {code!r}")
                continue
            if state.get(f"input_boolean.away_{zn}") != "on":
                log.debug(f"keenect: dropping restored saved_setpoints for {zn} (away not currently on)")
                continue
            try:
                restored[zn] = {"heat": float(heat), "cool": float(cool)}
            except ValueError:
                continue
        if restored:
            _st["saved_setpoints"] = restored
            log.info(f"keenect: restored saved_setpoints for {len(restored)} away zone(s)")
    except Exception as e:
        log.warning(f"keenect: failed to restore saved_setpoints state: {e}")


def _check_vent_health():
    """Detect stale Keen vents and re-send last commanded position.

    A vent is healthy if EITHER its pressure or its temperature sensor has
    changed recently (see _sensor_liveness_age) — first_floor's servo has no
    health_sensors/health_sensors_temp and is exempt, same as before."""
    if not _enabled():
        return

    now_ts = time_mod.time()
    stale_vents = []  # (zone, vent_id, worst_sensor_id, reason)

    for zn, zone in ZONES.items():
        pressure_sensors = zone.get("health_sensors", [])
        temp_sensors = zone.get("health_sensors_temp", [])
        if not pressure_sensors and not temp_sensors:
            continue

        n = max(len(pressure_sensors), len(temp_sensors))
        for i in range(n):
            vent_id = zone["vents"][min(i, len(zone["vents"]) - 1)]
            p_sensor = pressure_sensors[i] if i < len(pressure_sensors) else None
            t_sensor = temp_sensors[i] if i < len(temp_sensors) else None

            readings = []  # (sensor_id, age_seconds_or_None, unavailable)
            for sensor_id in (p_sensor, t_sensor):
                if sensor_id is None:
                    continue
                try:
                    s = state.get(sensor_id)
                    if s in ("unavailable", "unknown", None):
                        readings.append((sensor_id, None, True))
                        continue
                    val = float(s)
                    age = _sensor_liveness_age(sensor_id, val, now_ts)
                    readings.append((sensor_id, age, False))
                except Exception as e:
                    log.debug(f"keenect: health check error {sensor_id}: {e}")

            if not readings:
                continue  # both reads errored this pass — try again next time

            available = [(sid, age) for sid, age, unavail in readings if not unavail]
            fresh = any([age <= VENT_STALE_SECONDS for _, age in available])
            if fresh:
                continue

            if available:
                worst_sensor, worst_age = max(available, key=lambda t: t[1])
                reason = f"stale {int(worst_age / 60)}m"
            else:
                worst_sensor = readings[0][0]
                reason = "unavailable"
            stale_vents.append((zn, vent_id, worst_sensor, reason))

    if not stale_vents:
        # Clear notification if vents recovered
        try:
            persistent_notification.dismiss(notification_id="keenect_stale_vents")
        except Exception:
            pass
        return

    # Re-send last commanded position to stale cover vents
    for zn, vent_id, sensor_id, reason in stale_vents:
        zone = ZONES[zn]
        key = f"{zn}:{vent_id}"
        last_level = _st["vent_levels"].get(key)
        log.warning(f"keenect: vent {vent_id} ({zn}) {reason} "
                    f"(sensor: {sensor_id})")

        if last_level is not None and zone.get("vent_type") == "light":
            log.info(f"keenect: re-sending {vent_id} -> {last_level}%")
            try:
                if last_level == 0:
                    light.turn_off(entity_id=vent_id)
                else:
                    light.turn_on(entity_id=vent_id, brightness_pct=last_level)
            except Exception as e:
                log.error(f"keenect: re-send to {vent_id} failed: {e}")

    # Persistent notification in HA — names the actually-stale sensor, not
    # just the vent, and reports the true (value-change-based) age.
    vent_list = ", ".join(
        [f"{vid} [{sid}] ({reason})" for _, vid, sid, reason in stale_vents]
    )
    try:
        persistent_notification.create(
            title="Keenect: Stale Vent Detected",
            message=f"Vents may be offline: {vent_list}. "
                    f"Commands re-sent. Check Zigbee connectivity.",
            notification_id="keenect_stale_vents",
        )
    except Exception as e:
        log.error(f"keenect: notification failed: {e}")


# ---------------------------------------------------------------------------
# Pyscript triggers
# ---------------------------------------------------------------------------

@time_trigger("startup")
def on_startup():
    """Restore persisted state and re-evaluate after HA restart."""
    safety_synced = False
    try:
        _build_user_cache()
        # Load zone config from HA helpers (with hardcoded fallback)
        global ZONES, PASSIVE_ZONES, ZONE_DEFAULTS
        _populate_dropdowns()
        _restore_zone_selects()  # restore persisted dropdown values after options are populated
        _load_zone_config()
        _build_derived_maps()
        if not ZONES:
            log.warning("keenect: no zones from helpers, using hardcoded fallback")
            ZONES = _HARDCODED_ZONES
            PASSIVE_ZONES = _HARDCODED_PASSIVE
            ZONE_DEFAULTS = _HARDCODED_DEFAULTS
            _build_derived_maps()
        _load_state()
        log.info(
            f"keenect: startup - hvac_on={_st['hvac_on']} "
            f"main={_st['main_state']} recirc={_st['recirc_active']}"
        )
        # After reload, hardware state is unknown — reset so eval re-sends vent commands.
        # Keep zone_states from persisted state! Clearing them breaks stateful hysteresis:
        # zones in the deadband (at setpoint) would all evaluate as IDLE and close vents
        # even while the furnace is still running.
        was_main_state = _st["main_state"]  # persisted belief, for logging only

        # P9: Reconcile hvac_on/main_state against the ESPHome relays directly instead
        # of trusting the persisted blob. The persisted "hvac_on"/"main_state" reflect
        # software belief from before the restart, and a restart can land squarely
        # inside the MIN_RUN_TIME hold in _hvac_turn_off() — that guard returns before
        # touching hvac_on/main_state, so both can still say "actively cooling/heating"
        # (correctly) while HA is down. The relays themselves survive the restart
        # untouched, so they're the only source that tracks reality at this point.
        # Real incident (2026-07-31): cool relay physically ON, demand already zero,
        # restart mid-hold — blindly forcing hvac_on=False here let the circulation
        # reconciliation below treat the system as idle and open every zone's vent
        # while the compressor was still running.
        # state.get() can raise on an entity that has never been registered
        # (confirmed elsewhere in this file, e.g. _float()'s try/except) — these
        # two ESPHome switches always exist once the integration is set up, and
        # _hvac_push()/_eval_master()'s bypass check already call state.get() on
        # them unwrapped, constantly, without issue. Still, wrap defensively
        # here: any exception degrades to "unknown", the same as unavailable.
        try:
            heat_state = state.get("switch.hvac_controller_heat")
        except Exception as e:
            heat_state = None
            log.warning(f"keenect: startup - heat relay state read failed, treating as unknown: {e}")
        try:
            cool_state = state.get("switch.hvac_controller_cool")
        except Exception as e:
            cool_state = None
            log.warning(f"keenect: startup - cool relay state read failed, treating as unknown: {e}")
        heat_relay_on = heat_state == "on"
        cool_relay_on = cool_state == "on"
        if heat_relay_on or cool_relay_on:
            _st["hvac_on"] = True
            reconciled_state = "COOLING" if cool_relay_on else "HEATING"
            _st["main_state"] = reconciled_state
            # FIX 2 (2.6.7): last_hvac_on_time now survives restarts via
            # _load_state() (bounds-checked there) instead of always being 0
            # here, so MIN_RUN_TIME can span a restart when the persisted
            # state actually describes THIS run. But when the live relay
            # DISAGREES with the persisted main_state (was_main_state, read
            # before this block ran), the persisted last_hvac_on_time
            # describes whatever run main_state THOUGHT was happening — not
            # necessarily this one — so its true age is unknown. Same
            # reasoning 2.6.6 already used to justify leaving it at 0
            # unconditionally: arming a guard off an unverifiable timestamp
            # would recreate the exact window that fix closed (relay on,
            # guard blocking, circulation suppressed indefinitely). Zero it
            # in that case only — 0 disarms MIN_RUN_TIME, which fails toward
            # allowing the next eval to shut the relay off, never toward
            # blocking a shutoff demand already calls for.
            if was_main_state != reconciled_state:
                discarded = _st.get("last_hvac_on_time", 0)
                _st["last_hvac_on_time"] = 0
                log.warning(
                    f"keenect: startup - main_state mismatch (persisted={was_main_state} "
                    f"reconciled={reconciled_state}), discarding restored "
                    f"last_hvac_on_time={discarded:.0f} — true on-time unknown"
                )
            log.warning(
                f"keenect: startup - relay physically ON (heat={heat_relay_on} "
                f"cool={cool_relay_on}) despite persisted main_state={was_main_state}, "
                f"reconciling hvac_on=True so circulation stays suppressed until eval "
                f"turns the relay off (last_hvac_on_time={_st['last_hvac_on_time']:.0f})"
            )
        else:
            _st["hvac_on"] = False
            _st["main_state"] = "IDLE"
        was_on = _st["hvac_on"]  # reconciled value — drives the Zigbee-wait gate below
        _st["warmup_start"] = None
        _st["vent_levels"] = {}  # force vent re-sends

        # P6: Hardware safety sync — ensure the furnace is off before startup logic runs.
        # Skip the off-command unless BOTH: (a) the relay reads a definite "off" —
        # not "on", and not unavailable/unknown, and (b) the persisted main_state
        # doesn't itself claim that mode was active. Sending heatOff when heat is
        # physically on (or coolOff when cool is) fires on_turn_off on the ESP32 with
        # the relay still ON, which stamps *_last_off = millis(). The short-cycle
        # guard then blocks the next eval-driven turn-on for 120/300 s, and keenect's
        # 60 s heartbeat re-blocks it every retry — permanent livelock (this project
        # has lived that exact incident: 33h with no cooling, survived an ESP32
        # restart). An unavailable/unknown read is the likely case right after a
        # restart (same ESPHome re-initialization window as the rest of this fix),
        # so it must fail toward NOT pushing, same as a confirmed "on" read — union
        # of both conditions required (a) and (b), not just (a). Skipping a push to
        # an already-off relay costs nothing: eval_master() re-arms/re-clears the
        # correct relay at the end of startup regardless.
        log.info(
            "keenect: startup - hardware safety sync "
            f"(was_on={was_on} was_main_state={was_main_state} "
            f"heat_state={heat_state} cool_state={cool_state})"
        )
        if heat_state == "off" and was_main_state != "HEATING":
            _hvac_push(3)  # heatOff — confirmed off, and persisted state agrees
        if cool_state == "off" and was_main_state != "COOLING":
            _hvac_push(5)  # coolOff — confirmed off, and persisted state agrees
        if not _circ_enabled():
            _hvac_push(7)  # fanOff
        safety_synced = True

        if was_on:
            log.info("keenect: startup - was_on=True, will re-arm via eval")
        log.info(f"keenect: startup - restored zone_states={_st['zone_states']}")
        _restore_setpoint_log()
        _restore_vent_health_state()
        _restore_saved_setpoints_state()
        _update_status()
        _update_setpoint_log_sensor()
        _update_cost_sensors()  # publish initial sensor values
        state.set("sensor.keenect_anomalies", "OK", {
            "friendly_name": "HVAC Anomalies",
            "icon": "mdi:check-circle",
            "anomalies": [],
        })
        # Run zone learning on startup (populates vent factors)
        try:
            rates = _learn_zone_rates()
            if rates:
                factors = _compute_vent_factors(rates)
                _st["zone_rates"] = rates
                _st["zone_vent_factors"] = factors
                _update_zone_rates_sensor(rates, factors)
                log.info(f"keenect: startup zone learning - {len(rates)} zones analyzed")
            drift = _learn_drift_rates()
            if drift:
                _st["zone_drift"] = drift
                _update_drift_sensors(drift)
                log.info(f"keenect: startup drift analysis - {len(drift)} zones")
        except Exception as e:
            log.warning(f"keenect: startup zone learning failed: {e}")
        # Apply away setpoints for zones already in away mode. FIX B
        # (2.6.10): if _restore_saved_setpoints_state() above already
        # repopulated saved_setpoints[zn] for this zone (the normal restart-
        # while-away case, where setpoints are already correctly at the away
        # values so the `if` below is False and this block wouldn't otherwise
        # touch saved_setpoints at all), don't overwrite it with `cur_heat`/
        # `cur_cool` here — those may not be the true pre-away values (e.g. a
        # setpoint was edited moments before the restart). Only capture
        # current values as "saved" when nothing was restored.
        away_heat = _float("input_number.away_heat_setpoint", 55.0)
        away_cool = _float("input_number.away_cool_setpoint", 85.0)
        for zn in ZONES:
            if state.get(f"input_boolean.away_{zn}") == "on":
                cur_heat = _float(f"input_number.{zn}_heat_setpoint")
                cur_cool = _float(f"input_number.{zn}_cool_setpoint")
                if cur_heat is not None and (cur_heat != away_heat or cur_cool != away_cool):
                    if zn not in _st.get("saved_setpoints", {}):
                        _st.setdefault("saved_setpoints", {})[zn] = {
                            "heat": cur_heat,
                            "cool": cur_cool,
                        }
                        _save_saved_setpoints_state()
                    input_number.set_value(entity_id=f"input_number.{zn}_heat_setpoint", value=away_heat)
                    input_number.set_value(entity_id=f"input_number.{zn}_cool_setpoint", value=away_cool)
                    log.info(f"keenect: startup - {zn} away, setpoints → {away_heat}/{away_cool}")
                else:
                    log.info(f"keenect: startup - {zn} away, setpoints already correct")

        # P6: Zigbee delay — if system was active, wait 90s for Zigbee mesh to stabilize
        # before sending vent commands. Keen vents take time to rejoin after HA restart.
        if was_on:
            log.info("keenect: startup - waiting 90s for Zigbee mesh (was_on=True)")
            task.sleep(90)

        if _enabled():
            # _eval_master will: re-evaluate zones (with persisted state for hysteresis),
            # re-send vent commands (vent_levels cleared), and turn furnace on/off as needed.
            _eval_master()
            # Close all vents if IDLE after eval — if zones are IDLE→IDLE after restart,
            # _apply_zone_vents won't fire any vent commands, leaving vents
            # at whatever physical position they were in before restart.
            if not _st["hvac_on"] and not _st["recirc_active"]:
                if not _circ_enabled() and _all_idle():
                    log.info("keenect: startup - all idle, closing all vents")
                    _close_all_vents()
            # If continuous circulation is active, open vents and fan
            # (state_trigger won't fire since boolean was already on before reload).
            # Routed through _circ_start_effects() (rather than the old inline
            # loop) so its hvac_on guard applies here too — this call site used
            # to open circulation vents unconditionally, even if hvac_on was
            # still True (e.g. reload during an active run or a MIN_RUN_TIME
            # hold), which is exactly the bug this guard exists to prevent.
            if _circ_enabled():
                log.info("keenect: startup - circulation active, reconciling vents + fan")
                _circ_start_effects()
            _check_consistency()
    except Exception as e:
        log.error(f"keenect: on_startup crashed: {e}")
    finally:
        # If safety sync didn't complete, force everything off as last resort
        if not safety_synced:
            log.error("keenect: startup safety sync INCOMPLETE — forcing off")
            try:
                _hvac_push(3)  # heatOff
                _hvac_push(5)  # coolOff
                _hvac_push(7)  # fanOff
            except Exception as e2:
                log.error(f"keenect: startup finally block failed: {e2}")


@state_trigger("input_button.keenect_apply_config")
def _on_apply_config(**kwargs):
    """Reload zone config when user clicks Apply."""
    global ZONES, PASSIVE_ZONES, ZONE_DEFAULTS
    log.info("keenect: applying config changes")
    _persist_zone_config()  # Save selections to input_text first
    _load_zone_config()
    _build_derived_maps()
    if not ZONES:
        log.warning("keenect: no zones from helpers after apply, using hardcoded fallback")
        ZONES = _HARDCODED_ZONES
        PASSIVE_ZONES = _HARDCODED_PASSIVE
        ZONE_DEFAULTS = _HARDCODED_DEFAULTS
        _build_derived_maps()

    # Reconcile runtime state with new zone list
    old_zones = set(_st.get("zone_states", {}).keys())
    new_zones = set(ZONES.keys())

    # Remove stale zones from runtime state
    for removed in old_zones - new_zones:
        log.info(f"keenect: removing stale zone '{removed}' from runtime state")
        _st.get("zone_states", {}).pop(removed, None)
        _st.get("vent_levels", {}).pop(removed, None)
        _st.get("sensor_fail_count", {}).pop(removed, None)

    # Initialize new zones
    for added in new_zones - old_zones:
        log.info(f"keenect: initializing new zone '{added}' in runtime state")
        _st.setdefault("zone_states", {})[added] = "IDLE"
        _st.setdefault("vent_levels", {})[added] = 0
        _st.setdefault("sensor_fail_count", {})[added] = 0

    _update_status()
    # Turn the button back off
    # input_button has no state to reset — press action is momentary
    log.info(f"keenect: config applied — {len(ZONES)} zones active")


@service
def keenect_migrate_config():
    """One-time: write current zone config to HA helpers for GUI editing."""
    _populate_dropdowns()
    task.sleep(2)  # let options propagate

    # Zone 1 = ben
    _set_zone_helper(1, "ben", "sensor.gw1000_temp_ch7", "climate.ben_s_room",
                     "light.keen_ben", "(none)", "light", "Aggressive",
                     15, 100, 15, 100, 30, 62, 76)
    # Zone 2 = gene
    _set_zone_helper(2, "gene", "sensor.gw1000_temp_ch6", "climate.gene_s_room",
                     "light.keen_gene", "(none)", "light", "Aggressive",
                     15, 100, 15, 100, 30, 62, 76)
    # Zone 3 = mbr
    _set_zone_helper(3, "mbr", "sensor.gw1000_temp_ch4", "climate.master_bedroom",
                     "light.keen_mbr_1", "light.keen_mbr_2", "light", "Aggressive",
                     15, 100, 15, 100, 30, 64, 76)
    # Zone 4 = first_floor
    _set_zone_helper(4, "first_floor", "sensor.gw1000_indoor_temperature", "climate.first_floor",
                     "number.hvac_1st_floor_register_servo_angle", "(none)", "number", "Aggressive",
                     7, 45, 7, 45, 15, 69, 76)

    # Passive zones
    _set_passive_helper(1, "master_bath", "sensor.master_bathroom_temperature_temperature")
    _set_passive_helper(2, "office", "sensor.office_temperature_sonoff_temperature")
    _set_passive_helper(3, "basement", "sensor.basement_sonoff_temperature")
    _set_passive_helper(4, "guest_bedroom", "sensor.guest_bedroom_sonoff_temperature")

    task.sleep(1)  # let HA process the select_option calls
    _persist_zone_config()  # Persist to backing store for restart survival
    log.info("keenect: migration complete — config written to HA helpers and persisted")


def _set_zone_helper(slot, name, temp_sensor, thermostat, vent_1, vent_2,
                     vent_type, vent_control, h_min, h_max, c_min, c_max, fan,
                     heat_def, cool_def):
    pfx = f"keenect_zone_{slot}"
    input_boolean.turn_on(entity_id=f"input_boolean.{pfx}_enabled")
    input_text.set_value(entity_id=f"input_text.{pfx}_name", value=name)
    input_select.select_option(entity_id=f"input_select.{pfx}_temp_sensor", option=temp_sensor)
    input_select.select_option(entity_id=f"input_select.{pfx}_thermostat", option=thermostat)
    input_select.select_option(entity_id=f"input_select.{pfx}_vent_1", option=vent_1)
    input_select.select_option(entity_id=f"input_select.{pfx}_vent_2", option=vent_2)
    input_select.select_option(entity_id=f"input_select.{pfx}_vent_type", option=vent_type)
    input_select.select_option(entity_id=f"input_select.{pfx}_vent_control", option=vent_control)
    input_number.set_value(entity_id=f"input_number.{pfx}_heat_min_vo", value=h_min)
    input_number.set_value(entity_id=f"input_number.{pfx}_heat_max_vo", value=h_max)
    input_number.set_value(entity_id=f"input_number.{pfx}_cool_min_vo", value=c_min)
    input_number.set_value(entity_id=f"input_number.{pfx}_cool_max_vo", value=c_max)
    input_number.set_value(entity_id=f"input_number.{pfx}_fan_vo", value=fan)
    input_number.set_value(entity_id=f"input_number.{pfx}_heat_default", value=heat_def)
    input_number.set_value(entity_id=f"input_number.{pfx}_cool_default", value=cool_def)


def _set_passive_helper(slot, name, temp_sensor):
    pfx = f"keenect_passive_{slot}"
    input_boolean.turn_on(entity_id=f"input_boolean.{pfx}_enabled")
    input_text.set_value(entity_id=f"input_text.{pfx}_name", value=name)
    input_select.select_option(entity_id=f"input_select.{pfx}_temp_sensor", option=temp_sensor)


@time_trigger("period(now, 15s)")
def periodic_eval():
    """Evaluate every 15 seconds (matches Hubitat schedule)."""
    try:
        _verify_vents()
        _eval_master()
    except Exception as e:
        log.error(f"keenect: periodic_eval crashed: {e}")


@state_trigger(
    "climate.ben_s_room",
    "climate.gene_s_room",
    "climate.master_bedroom",
    "climate.first_floor",
    "sensor.gw1000_temp_ch4",
    "sensor.gw1000_temp_ch6",
    "sensor.gw1000_temp_ch7",
    "sensor.gw1000_indoor_temperature",
)
def on_climate_change(**kwargs):
    """React to climate or raw temp sensor changes."""
    try:
        log.info(f"keenect: climate change {kwargs.get('var_name')}")
        _eval_master()
    except Exception as e:
        log.error(f"keenect: on_climate_change crashed: {e}")


@state_trigger("input_select.hvac_mode")
def on_mode_change(**kwargs):
    try:
        mode = state.get("input_select.hvac_mode")
        log.info(f"keenect: HVAC mode -> {mode}")
        if mode == "OFF":
            if _st["hvac_on"]:
                _hvac_turn_off()
            _close_all_vents()
            _save_if_changed()
        else:
            # Mode changed (HEAT<->COOL): turn off first if running in the other mode
            if _st["hvac_on"]:
                _hvac_turn_off()
            _eval_master()
    except Exception as e:
        log.error(f"keenect: on_mode_change crashed: {e}")


@state_trigger("input_boolean.keenect_enabled")
def on_enable_change(**kwargs):
    try:
        on = state.get("input_boolean.keenect_enabled") == "on"
        log.info(f"keenect: {'enabled' if on else 'disabled'}")
        if on:
            _eval_master()
        else:
            if _st["hvac_on"]:
                _hvac_turn_off()
            if _st["recirc_active"]:
                _stop_recirc("Disabled")
            _save_if_changed()
    except Exception as e:
        log.error(f"keenect: on_enable_change crashed: {e}")


def _relays_confirmed_off():
    """True only when BOTH heat and cool relay switches report a definite
    "off" — not "on", and not unavailable/unknown/None.

    Deliberately a separate, stricter check from the hvac_on reconciliation in
    on_startup (which treats an unavailable read as "not on", i.e. fails
    toward False — that direction protects against auto-disabling keenect via
    push_fail_count on an ordinary cold boot, and must not change). Starting
    circulation is a comfort/efficiency action with no safety role, so it can
    afford to wait: this fails the OTHER direction, toward NOT starting until
    the relays are positively confirmed idle, rather than assuming idle just
    because hvac_on says so. That matters right after a HA restart, when the
    ESPHome switches can still read unavailable/unknown for the first several
    seconds while entities re-initialize — exactly the window the real
    2026-07-31 incident's restart landed in.
    """
    # state.get() raises on an entity that was never registered — not the case
    # here (see on_startup) — but degrade to "not confirmed off" rather than
    # propagate, matching this function's own fail-toward-not-starting stance.
    try:
        heat_state = state.get("switch.hvac_controller_heat")
    except Exception:
        heat_state = None
    try:
        cool_state = state.get("switch.hvac_controller_cool")
    except Exception:
        cool_state = None
    return heat_state == "off" and cool_state == "off"


def _circ_start_effects():
    """Open non-excluded zone vents to fan position and turn the blower on.

    Shared by on_circ_change (enable_circulation -> on), on_electricity_price_
    change (on-peak window ends while circulation should be running), and
    on_startup (continuous circulation already on before a reload) so all
    callers can't drift apart.

    Refuses to run while _st["hvac_on"] is True. This is the same flag
    _hvac_turn_off()/_hvac_turn_on() flip only around successful relay
    pushes, so it tracks the actual heat/cool relay, not just computed zone
    demand. Critically, it stays True through the MIN_RUN_TIME hold in
    _hvac_turn_off() (the guard returns before touching it) — the compressor
    or heat relay is still physically energized during that hold even though
    demand has already dropped to zero. Circulation must never open zone
    vents while the relay is still actively conditioning; once
    _hvac_turn_off() actually completes, hvac_on flips False and the next
    eval/trigger runs this normally (self-healing, no deadlock).

    Also refuses to run unless _relays_confirmed_off() — see that function's
    docstring. hvac_on alone isn't enough right after a restart: on_startup
    can reconcile hvac_on=False purely because the ESPHome switches hadn't
    reported in yet, not because the relay is actually off.
    """
    if _st["hvac_on"]:
        log.info("keenect: circulation start skipped — HVAC relay still active")
        return
    if not _relays_confirmed_off():
        log.info("keenect: circulation start skipped — relay state not yet confirmed off")
        return
    for zn, zone in ZONES.items():
        if not _zone_circ_excluded(zn):
            _set_vent(zn, zone.get("fan_vo", 30))
    _hvac_push(6)  # fanOn


def _circ_stop_effects():
    """Turn the blower off and close vents, unless HVAC or recirc is active.

    Shared by on_circ_change (enable_circulation -> off) and
    on_electricity_price_change (on-peak window begins while circulation was
    running). Demand priority is preserved here: if hvac_on, nothing happens
    — heat/cool owns the fan/vents until it finishes on its own.
    """
    if not _st["hvac_on"] and not _st["recirc_active"]:
        _hvac_push(7)  # fanOff
        if _all_idle():
            _close_all_vents()


@state_trigger("input_boolean.enable_circulation")
def on_circ_change(**kwargs):
    """React to continuous circulation toggle."""
    try:
        on = _circ_enabled()
        log.info(f"keenect: continuous circulation {'ON' if on else 'OFF'}")
        if on:
            if _st["hvac_on"]:
                # HVAC is active — do NOT stomp zone vent positions. _hvac_turn_off
                # already checks _circ_enabled() and keeps the fan on + skips vent
                # closure, so circulation takes effect automatically once the
                # heat/cool run ends. (Prevents circulation overriding heat/cool.)
                log.info("keenect: circulation enabled while HVAC active — deferring vents until idle")
            else:
                _circ_start_effects()
        else:
            _circ_stop_effects()
        _update_status()
        _save_if_changed()
    except Exception as e:
        log.error(f"keenect: on_circ_change crashed: {e}")


def _reconcile_peak_lockout(reason):
    """Re-evaluate circulation against the current on-peak lockout state.

    Called whenever a signal feeding _peak_lockout() changes (the TOU period,
    or the user's circ_peak_lockout escape-hatch toggle) so the fan/vents
    don't have to wait for the next 15s eval-master pass or the next Periodic
    :00/:10 trigger to catch up — that matters most for Continuous mode,
    which never re-fires input_boolean.enable_circulation itself, and for the
    manual toggle, whose only other feedback path (eval-master idle
    reconciliation) fixes vent positions but never the fan relay.

    Only touches fan-only circulation/recirculation — heat/cool demand is
    never gated here, and _circ_stop_effects/_stop_recirc both no-op the
    fan/vent changes while hvac_on so an active heat/cool run is untouched.
    """
    locked = _peak_lockout()

    # Smart recirculation: end it immediately if lockout just engaged.
    if locked and _st["recirc_active"]:
        _stop_recirc("On-peak lockout")

    # Periodic/Continuous circulation: reconcile fan+vents to the new state.
    if state.get("input_boolean.enable_circulation") == "on":
        if locked:
            log.info(f"keenect: on-peak lockout engaged ({reason}) — suspending circulation")
            _circ_stop_effects()
        elif not _st["hvac_on"]:
            log.info(f"keenect: on-peak lockout cleared ({reason}) — resuming circulation")
            _circ_start_effects()

    _update_status()
    _save_if_changed()


@state_trigger("sensor.electricity_price")
def on_electricity_price_change(**kwargs):
    """Re-evaluate circulation when the TOU period changes (on-peak boundary)."""
    try:
        _reconcile_peak_lockout("price period changed")
    except Exception as e:
        log.error(f"keenect: on_electricity_price_change crashed: {e}")


@state_trigger("input_boolean.circ_peak_lockout")
def on_peak_lockout_toggle_change(**kwargs):
    """React to the user's on-peak lockout escape-hatch toggle."""
    try:
        _reconcile_peak_lockout("lockout toggle changed")
    except Exception as e:
        log.error(f"keenect: on_peak_lockout_toggle_change crashed: {e}")


@state_trigger("input_boolean.circ_optout_ben", "input_boolean.circ_optout_gene",
               "input_boolean.circ_optout_mbr", "input_boolean.circ_optout_first_floor")
def on_circ_optout_change(**kwargs):
    """React to per-zone circulation opt-out toggle."""
    try:
        if not _circ_enabled() and not _st["recirc_active"]:
            return  # nothing to do if circulation isn't running
        if _st["hvac_on"]:
            return  # L6: don't stomp zone vents during active HVAC run
        eid = kwargs.get("var_name", "")
        # Extract zone name: input_boolean.circ_optout_ben -> ben
        zn = eid.replace("input_boolean.circ_optout_", "")
        if zn not in ZONES:
            return
        zone = ZONES[zn]
        if _zone_circ_excluded(zn):
            log.info(f"keenect: {zn} opted out of circulation, closing vent")
            _set_vent(zn, 0)
        else:
            log.info(f"keenect: {zn} opted back into circulation, opening vent")
            _set_vent(zn, zone.get("fan_vo", 30))
        _update_status()
    except Exception as e:
        log.error(f"keenect: on_circ_optout_change crashed: {e}")


@state_trigger(
    "input_number.ben_heat_setpoint", "input_number.ben_cool_setpoint",
    "input_number.gene_heat_setpoint", "input_number.gene_cool_setpoint",
    "input_number.mbr_heat_setpoint", "input_number.mbr_cool_setpoint",
    "input_number.first_floor_heat_setpoint", "input_number.first_floor_cool_setpoint",
)
def on_setpoint_change(**kwargs):
    """Track who changed a thermostat setpoint, and react immediately instead
    of waiting up to 15s for periodic_eval.

    FIX 4 (2.6.7): calls _eval_master() directly. Concurrency safety is
    reused, not reinvented — _eval_master() already gates itself with its
    own debounce_until re-entrancy check (set synchronously, before any
    await point), so this can't run concurrently with periodic_eval's 15s
    timer or any other trigger that calls _eval_master(). That same 2s
    window also debounces a rapid slider drag: intermediate values collapse
    into the first eval. FIX (2.6.8): if this call lands inside someone
    else's debounce window it no longer just waits for the next periodic_eval
    tick — _eval_master() spawns _eval_followup() to replay the eval as soon
    as the window clears (see that function's docstring).

    Attribution (FIX A, 2.6.10): real-user resolution (context, then the
    setpoint_user_pending stash on_climate_set_temperature/on_away_change
    populate) always runs first and, if it yields a name, that name wins —
    never overridden below. ONLY when it still resolves to the generic
    "System" fallback do we check whether this looks like an away-mode
    reclamp: zone currently in away mode AND the new value exactly equals
    the away setpoint for this heat/cool attribute. This replaced a timing-
    based stash in _enforce_away_setpoints() that could clobber a real
    user's pending entry (see that function's docstring) — equality-checking
    here instead is deterministic and independent of call ordering. Residual
    edge case (accepted): a direct input_number/backend write with no user
    context that happens to numerically equal the away value would still be
    labeled "Away" even though not actually caused by away logic.
    """
    try:
        eid = kwargs.get("var_name", "")
        mapping = _SETPOINT_MAP.get(eid)
        if not mapping:
            return
        _eval_master()
        zone, sp_type = mapping
        old_val = kwargs.get("old_value")
        new_val = kwargs.get("value")
        # Skip non-numeric transitions (e.g., "unknown" -> "69.0" on startup)
        try:
            old_f = float(old_val)
            new_f = float(new_val)
        except (TypeError, ValueError):
            return
        if abs(old_f - new_f) < 0.5:
            return
        context = kwargs.get("context")
        user_id = context.user_id if context else None
        user = _resolve_user(user_id)
        if user == "System":
            pending = _st["setpoint_user_pending"].get(zone)
            if pending and (time_mod.time() - pending[1]) < 10:
                user = pending[0]
                _st["setpoint_user_pending"].pop(zone, None)
        if user == "System" and state.get(f"input_boolean.away_{zone}") == "on":
            away_heat = _float("input_number.away_heat_setpoint", 55.0)
            away_cool = _float("input_number.away_cool_setpoint", 85.0)
            away_target = away_heat if sp_type == "heat" else away_cool
            if away_target is not None and new_f == away_target:
                user = "Away"

        now = dt_mod.datetime.now()
        entry = {
            "time": now.strftime("%m/%d %I:%M %p"),
            "zone": zone.replace("_", " ").title(),
            "type": sp_type.title(),
            "old": int(old_f) if old_f % 1 == 0 else old_f,
            "new": int(new_f) if new_f % 1 == 0 else new_f,
            "user": user,
        }
        _st["setpoint_log"].insert(0, entry)
        _st["setpoint_log"] = _st["setpoint_log"][:_SETPOINT_LOG_MAX]

        log.info(f"keenect: setpoint change - {user} set {zone} {sp_type} {old_f}->{new_f}")
        _update_setpoint_log_sensor()
        _save_setpoint_log()
    except Exception as e:
        log.error(f"keenect: on_setpoint_change crashed: {e}")


@event_trigger("call_service", "domain == 'climate' and service == 'set_temperature'")
def on_climate_set_temperature(**kwargs):
    """Capture real user from climate.set_temperature before input_number chain loses context.

    When a user adjusts a thermostat via the HA UI, the call arrives as
    climate.set_temperature with a real user_id in context.  The climate_template
    YAML action then calls input_number.set_value with a fresh system context
    (user_id=None), so on_setpoint_change would record "System".  We stash the
    resolved user name here so on_setpoint_change can reclaim it within 10 s.
    """
    try:
        if kwargs.get("domain") != "climate" or kwargs.get("service") != "set_temperature":
            return
        ctx = kwargs.get("context") or {}
        user_id = ctx.get("user_id") if isinstance(ctx, dict) else getattr(ctx, "user_id", None)
        user = _resolve_user(user_id)
        sd = kwargs.get("service_data") or {}
        target = sd.get("target") or kwargs.get("target") or {}
        entity_ids = sd.get("entity_id") or (target.get("entity_id") if isinstance(target, dict) else None) or []
        if isinstance(entity_ids, str):
            entity_ids = [entity_ids]
        # Derive climate→zone mapping from ZONES (thermostat key)
        climate_to_zone = {zone["thermostat"]: zn for zn, zone in ZONES.items()}
        for eid in entity_ids:
            zone = climate_to_zone.get(eid)
            if zone:
                _st["setpoint_user_pending"][zone] = (user, time_mod.time())
    except Exception as e:
        log.error(f"keenect: on_climate_set_temperature crashed: {e}")


@state_trigger(
    "input_boolean.away_ben", "input_boolean.away_gene",
    "input_boolean.away_mbr", "input_boolean.away_first_floor",
)
def on_away_change(**kwargs):
    """Update thermostat setpoints when away mode is toggled.

    FIX B (2.6.10): saved_setpoints is persisted (SAVED_SETPOINTS_ENTITY) on
    every change here so it survives an HA restart — see
    _save_saved_setpoints_state()/_restore_saved_setpoints_state().
    """
    try:
        eid = kwargs.get("var_name", "")
        zone_name = eid.replace("input_boolean.away_", "")
        if zone_name not in ZONES:
            return
        new_val = kwargs.get("value")
        heat_key = f"input_number.{zone_name}_heat_setpoint"
        cool_key = f"input_number.{zone_name}_cool_setpoint"

        if new_val == "on":
            # Save current setpoints, then apply away values
            _st.setdefault("saved_setpoints", {})[zone_name] = {
                "heat": _float(heat_key),
                "cool": _float(cool_key),
            }
            _save_saved_setpoints_state()
            away_heat = _float("input_number.away_heat_setpoint", 55.0)
            away_cool = _float("input_number.away_cool_setpoint", 85.0)
            input_number.set_value(entity_id=heat_key, value=away_heat)
            input_number.set_value(entity_id=cool_key, value=away_cool)
            log.info(f"keenect: {zone_name} away ON — setpoints → {away_heat}/{away_cool}")
        elif new_val == "off":
            # Restore saved setpoints or zone defaults. Attribution (FIX A,
            # 2.6.10): stash "Away off" into setpoint_user_pending right
            # before each write, same mechanism on_climate_set_temperature
            # uses for real users — safe here (unlike the removed stash in
            # _enforce_away_setpoints) because this zone's away boolean is
            # already "off" by the time these writes land, so
            # _enforce_away_setpoints() won't touch this zone this pass and
            # can't clobber the stash before on_setpoint_change reads it.
            # Re-stashed before each of the two writes since the first
            # write's own on_setpoint_change call consumes (pops) the slot.
            saved = _st.get("saved_setpoints", {}).get(zone_name)
            defaults = ZONE_DEFAULTS.get(zone_name, {"heat": 62, "cool": 76})
            heat_restore = saved["heat"] if saved and saved.get("heat") is not None else defaults["heat"]
            cool_restore = saved["cool"] if saved and saved.get("cool") is not None else defaults["cool"]
            _st["setpoint_user_pending"][zone_name] = ("Away off", time_mod.time())
            input_number.set_value(entity_id=heat_key, value=heat_restore)
            _st["setpoint_user_pending"][zone_name] = ("Away off", time_mod.time())
            input_number.set_value(entity_id=cool_key, value=cool_restore)
            _st.get("saved_setpoints", {}).pop(zone_name, None)
            _save_saved_setpoints_state()
            log.info(f"keenect: {zone_name} away OFF — setpoints → {heat_restore}/{cool_restore}")
    except Exception as e:
        log.error(f"keenect: on_away_change crashed: {e}")


@time_trigger("cron(*/5 * * * *)")
def periodic_consistency():
    try:
        _check_consistency()
    except Exception as e:
        log.error(f"keenect: periodic_consistency crashed: {e}")


@time_trigger("cron(*/3 * * * *)")
def periodic_recirc():
    try:
        _check_recirc()
    except Exception as e:
        log.error(f"keenect: periodic_recirc crashed: {e}")


@time_trigger("cron(*/10 * * * *)")
def periodic_vent_health():
    """Check vent health every 10 minutes."""
    try:
        _check_vent_health()
        _save_vent_health_state()
    except Exception as e:
        log.error(f"keenect: periodic_vent_health crashed: {e}")


GAS_PRICE_URL = "https://gaschoice.apps.lara.state.mi.us/Choice/CurrentOffers?areaId=2&marketId=1"

@time_trigger("cron(0 6 * * 1)")  # Every Monday at 6 AM
def update_gas_price():
    """Fetch DTE gas price from Michigan comparison site and update helper."""
    try:
        resp = task.executor(urllib.request.urlopen, GAS_PRICE_URL, None, 15)
        html = resp.read().decode("utf-8")
        match = re_mod.search(r'id="price-to-compare">\s*\$([0-9.]+)/CCF', html)
        if not match:
            log.warning("keenect: could not parse DTE gas price from comparison site")
            return
        price = float(match.group(1))
        if price < 0.10 or price > 5.00:
            log.warning(f"keenect: gas price ${price} outside valid range, skipping")
            return
        current = _float("input_number.gas_rate_per_therm", 0)
        if abs(current - price) < 0.001:
            log.info(f"keenect: gas price unchanged at ${price}/CCF")
            return
        input_number.set_value(entity_id="input_number.gas_rate_per_therm", value=price)
        log.info(f"keenect: updated gas price ${current}→${price}/CCF from MI gas comparison")
    except Exception as e:
        log.error(f"keenect: failed to fetch gas price: {e}")


@time_trigger("cron(0 * * * *)")
def log_stats():
    try:
        log.info(
            f"keenect stats: state={_st['main_state']} hvac={_st['hvac_on']} "
            f"recirc={_st['recirc_active']} zones={_st['zone_states']} "
            f"vents={_st['vent_levels']}"
        )
    except Exception as e:
        log.error(f"keenect: log_stats crashed: {e}")


# ---------------------------------------------------------------------------
# Anomaly detection via InfluxDB
# ---------------------------------------------------------------------------
INFLUX_URL = "http://a0d7b954-influxdb:8086/query"
INFLUX_DB = "homeassistant"
ANOMALY_THRESHOLD = 1.0  # °F wrong-direction movement triggers alert
ANOMALY_WINDOW = 30  # minutes of history to analyze
ZONE_LEARN_DAYS = 7  # days of InfluxDB history for zone rate learning
ZONE_RATE_BOOST_MAX = 1.4  # max vent multiplier for slow zones
ZONE_RATE_BOOST_MIN = 0.7  # min vent multiplier for fast zones


def _influx_temps(entity_short, minutes=30):
    """Query InfluxDB for recent zone temps. Returns list of (time, value)."""
    query = (
        f'SELECT mean("value") FROM "°F" '
        f"WHERE \"entity_id\" = '{entity_short}' "
        f"AND time > now() - {minutes}m "
        f"GROUP BY time(5m) fill(previous)"
    )
    params = urllib.parse.urlencode({"db": INFLUX_DB, "q": query})
    url = f"{INFLUX_URL}?{params}"
    resp = task.executor(urllib.request.urlopen, url, None, 10)
    data = json_mod.loads(resp.read().decode())
    series = data.get("results", [{}])[0].get("series", [])
    if not series:
        return []
    return [(v[0], v[1]) for v in series[0].get("values", []) if v[1] is not None]


def _influx_series(entity_short, days=7, interval_min=30):
    """Query InfluxDB for multi-day temp series at given interval."""
    query = (
        f'SELECT mean("value") FROM "°F" '
        f"WHERE \"entity_id\" = '{entity_short}' "
        f"AND time > now() - {days}d "
        f"GROUP BY time({interval_min}m) fill(previous)"
    )
    params = urllib.parse.urlencode({"db": INFLUX_DB, "q": query})
    url = f"{INFLUX_URL}?{params}"
    resp = task.executor(urllib.request.urlopen, url, None, 15)
    data = json_mod.loads(resp.read().decode())
    series = data.get("results", [{}])[0].get("series", [])
    if not series:
        return []
    return [v[1] for v in series[0].get("values", []) if v[1] is not None]


def _learn_zone_rates():
    """Analyze InfluxDB history to compute zone heating/cooling rates (°F/hour)."""
    rates = {}
    for zone_name, zone in ZONES.items():
        entity_short = zone["temp_sensor"].replace("sensor.", "")
        try:
            temps = _influx_series(entity_short, days=ZONE_LEARN_DAYS, interval_min=30)
        except Exception as e:
            log.warning(f"keenect: zone learning query failed for {zone_name}: {e}")
            continue

        if len(temps) < 10:
            continue

        heating_rates = []
        cooling_rates = []
        for i in range(1, len(temps)):
            delta = temps[i] - temps[i - 1]
            rate_per_hour = delta * 2  # 30-min intervals → °F/hour
            if rate_per_hour > 0.3:
                heating_rates.append(rate_per_hour)
            elif rate_per_hour < -0.3:
                cooling_rates.append(abs(rate_per_hour))

        # Use median for robustness (outliers from door opens, etc.)
        heating_rates.sort()
        cooling_rates.sort()
        h_med = heating_rates[len(heating_rates) // 2] if heating_rates else 0
        c_med = cooling_rates[len(cooling_rates) // 2] if cooling_rates else 0

        rates[zone_name] = {
            "heat_rate": round(h_med, 2),
            "cool_rate": round(c_med, 2),
            "heat_samples": len(heating_rates),
            "cool_samples": len(cooling_rates),
        }

    return rates


def _compute_vent_factors(rates):
    """Compute per-zone vent multipliers from learned rates. Slow zones get boosted."""
    factors = {}
    for mode_key in ("heat_rate", "cool_rate"):
        mode_rates = {z: r[mode_key] for z, r in rates.items() if r[mode_key] > 0}
        if not mode_rates:
            continue
        avg = sum(mode_rates.values()) / len(mode_rates)
        if avg <= 0:
            continue
        for z, r in mode_rates.items():
            ratio = avg / r  # >1 for slow zones, <1 for fast zones
            factor = max(ZONE_RATE_BOOST_MIN, min(ZONE_RATE_BOOST_MAX, ratio))
            fk = "heat_factor" if mode_key == "heat_rate" else "cool_factor"
            factors.setdefault(z, {})[fk] = round(factor, 2)
    return factors


def _update_zone_rates_sensor(rates, factors):
    """Publish zone learning data as sensors (summary + per-zone)."""
    zone_data = {}
    for z, r in rates.items():
        hf = factors.get(z, {}).get("heat_factor", 1.0)
        cf = factors.get(z, {}).get("cool_factor", 1.0)
        zone_data[z] = {
            "heat_rate": f"{r['heat_rate']}°F/h",
            "cool_rate": f"{r['cool_rate']}°F/h",
            "heat_factor": hf,
            "cool_factor": cf,
        }
        # Per-zone sensor for dashboard display
        display = z.replace("_", " ").title()
        avg_factor = (hf + cf) / 2 if hf and cf else hf or cf or 1.0
        if avg_factor > 1.05:
            label = f"Boost ({avg_factor:.1f}x)"
            icon = "mdi:arrow-up-bold"
        elif avg_factor < 0.95:
            label = f"Reduced ({avg_factor:.1f}x)"
            icon = "mdi:arrow-down-bold"
        else:
            label = "Normal"
            icon = "mdi:minus"
        state.set(f"sensor.keenect_rate_{z}", label, {
            "friendly_name": f"{display} Rate",
            "icon": icon,
            "heat_rate": f"{r['heat_rate']}°F/h",
            "cool_rate": f"{r['cool_rate']}°F/h",
            "heat_factor": hf,
            "cool_factor": cf,
        })
    state.set("sensor.keenect_zone_rates", "Learned", {
        "friendly_name": "Zone Learning Rates",
        "icon": "mdi:chart-timeline-variant",
        "zones": zone_data,
    })


def _learn_drift_rates():
    """Analyze how fast each zone loses heat when HVAC is off (insulation metric)."""
    outdoor_temps = _influx_series(OUTDOOR_TEMP_ENTITY.replace("sensor.", ""), days=ZONE_LEARN_DAYS, interval_min=30)
    if len(outdoor_temps) < 10:
        log.warning("keenect: drift analysis - no outdoor temp data")
        return {}

    # Combine active zones and passive (sensor-only) zones for drift analysis
    all_drift_zones = {}
    for zone_name, zone in ZONES.items():
        all_drift_zones[zone_name] = zone["temp_sensor"]
    for zone_name, zone in PASSIVE_ZONES.items():
        all_drift_zones[zone_name] = zone["temp_sensor"]

    drift = {}
    for zone_name, temp_sensor in all_drift_zones.items():
        entity_short = temp_sensor.replace("sensor.", "")
        try:
            zone_temps = _influx_series(entity_short, days=ZONE_LEARN_DAYS, interval_min=30)
        except Exception:
            continue
        if len(zone_temps) < 10:
            continue

        # Use the shorter list length
        n = min(len(zone_temps), len(outdoor_temps))

        # Detect sensor resolution from minimum observed step
        deltas = [abs(zone_temps[i] - zone_temps[i - 1])
                  for i in range(1, len(zone_temps))
                  if abs(zone_temps[i] - zone_temps[i - 1]) > 0.01]
        min_step = min(deltas) if deltas else 0.1

        # Sliding window: coarse sensors (Sonoff ~1°F steps) need wider windows
        # to average out quantization artifacts. Fine sensors use single interval.
        if min_step > 0.3:
            window = min(int(min_step / 0.2) + 1, 12)  # cap at 6 hours
        else:
            window = 1
        window_hours = window * 0.5  # each interval is 30 min

        # Fixed ceiling — windowing handles coarse-sensor inflation
        ceiling = 0.08

        heat_loss_rates = []  # °F/h per °F delta (winter: indoor dropping toward outdoor)
        heat_gain_rates = []  # °F/h per °F delta (summer: indoor rising toward outdoor)

        for i in range(window, n):
            indoor = zone_temps[i - window]
            indoor_next = zone_temps[i]
            delta_indoor = indoor_next - indoor  # negative = cooling

            # Average outdoor temp over the window for stable normalization
            outdoor_sum = 0.0
            for j in range(i - window, i):
                outdoor_sum = outdoor_sum + outdoor_temps[j]
            outdoor = outdoor_sum / window

            diff = indoor - outdoor  # positive in winter (indoor warmer)

            if abs(diff) < 5:
                continue  # Not enough temp difference to measure drift

            rate_per_hour = delta_indoor / window_hours

            # Heat loss: indoor dropping, indoor > outdoor (winter drift)
            if diff > 5 and rate_per_hour < -0.05:
                normalized = abs(rate_per_hour) / diff
                if normalized < ceiling:
                    heat_loss_rates.append(normalized)

            # Heat gain: indoor rising, outdoor > indoor (summer drift)
            if diff < -5 and rate_per_hour > 0.05:
                normalized = rate_per_hour / abs(diff)
                if normalized < ceiling:
                    heat_gain_rates.append(normalized)

        heat_loss_rates.sort()
        heat_gain_rates.sort()

        # Use 25th percentile — natural drift is the slowest cooling rate
        loss_p25 = heat_loss_rates[len(heat_loss_rates) // 4] if heat_loss_rates else 0
        gain_p25 = heat_gain_rates[len(heat_gain_rates) // 4] if heat_gain_rates else 0

        drift[zone_name] = {
            "heat_loss": round(loss_p25 * 1000, 1),  # milli-°F/h per °F delta (easier to read)
            "heat_gain": round(gain_p25 * 1000, 1),
            "loss_samples": len(heat_loss_rates),
            "gain_samples": len(heat_gain_rates),
            "sensor_step": round(min_step, 2),
            "window_intervals": window,
        }

    return drift


def _update_drift_sensors(drift):
    """Publish drift rate sensors showing estimated overnight temp drop."""
    if not drift:
        return
    OVERNIGHT_HOURS = 8
    outdoor = _outdoor_temp()

    # Compute overnight drop for each zone (active + passive)
    drops = {}
    for z, d in drift.items():
        rate = d["heat_loss"] / 1000.0  # convert m°F/h/°F back to °F/h/°F
        zone = ZONES.get(z) or PASSIVE_ZONES.get(z)
        indoor = _float(zone["temp_sensor"]) if zone else None
        if indoor is not None and outdoor is not None and rate > 0:
            diff = abs(indoor - outdoor)
            drop = round(rate * OVERNIGHT_HOURS * diff, 1)
        else:
            drop = round(rate * OVERNIGHT_HOURS * 40, 1)  # assume 40°F diff as fallback
        drops[z] = drop

    ranked = sorted(drops.items(), key=lambda x: x[1], reverse=True)
    worst_zone = ranked[0][0] if ranked else ""
    avg_drop = sum([v for v in drops.values()]) / len(drops) if drops else 0

    for z, d in drift.items():
        display = z.replace("_", " ").title()
        drop = drops.get(z, 0)
        if drop == 0:
            label = "No data"
            icon = "mdi:help-circle"
        elif z == worst_zone and len(drift) > 1:
            label = f"~{drop}°F/night (leakiest)"
            icon = "mdi:thermometer-alert"
        elif drop > avg_drop * 1.2:
            label = f"~{drop}°F/night"
            icon = "mdi:thermometer-minus"
        else:
            label = f"~{drop}°F/night"
            icon = "mdi:thermometer-check"
        state.set(f"sensor.keenect_drift_{z}", label, {
            "friendly_name": f"{display} Overnight Drop",
            "icon": icon,
            "overnight_drop": drop,
            "heat_loss_rate": d["heat_loss"],
            "heat_gain_rate": d["heat_gain"],
            "sensor_step": d.get("sensor_step", 0),
            "window_intervals": d.get("window_intervals", 1),
        })

    state.set("sensor.keenect_drift", f"{len(drift)} zones", {
        "friendly_name": "Overnight Heat Loss",
        "icon": "mdi:home-thermometer",
        "zones": {z: {"overnight_drop": drops.get(z, 0), **d} for z, d in drift.items()},
    })


@time_trigger("cron(0 3 * * *)")
def daily_zone_learning():
    """Daily zone rate + drift analysis from InfluxDB history."""
    try:
        rates = _learn_zone_rates()
        if not rates:
            log.warning("keenect: zone learning found no data")
            return
        factors = _compute_vent_factors(rates)
        _st["zone_rates"] = rates
        _st["zone_vent_factors"] = factors
        _update_zone_rates_sensor(rates, factors)

        drift = _learn_drift_rates()
        if drift:
            _st["zone_drift"] = drift
            _update_drift_sensors(drift)
            log.info(f"keenect: drift analysis complete - {drift}")

        log.info(f"keenect: zone learning complete - rates={rates} factors={factors}")
    except Exception as e:
        log.error(f"keenect: daily_zone_learning crashed: {e}")


def _detect_anomalies():
    """Check each zone for temperature moving wrong direction while HVAC active."""
    if not _st.get("hvac_on"):
        return []
    mode = _hvac_mode()
    if mode not in ("HEAT", "COOL"):
        return []

    anomalies = []
    for zone_name, zone in ZONES.items():
        sensor = zone["temp_sensor"]
        entity_short = sensor.replace("sensor.", "")
        current_temp = _float(sensor)
        if current_temp is None:
            continue

        sp_key = (
            f"input_number.{zone_name}_heat_setpoint" if mode == "HEAT"
            else f"input_number.{zone_name}_cool_setpoint"
        )
        setpoint = _float(sp_key)
        if setpoint is None:
            continue

        # Only check zones that are demanding (not yet satisfied)
        if mode == "HEAT" and current_temp >= setpoint:
            continue
        if mode == "COOL" and current_temp <= setpoint:
            continue

        try:
            temps = _influx_temps(entity_short, ANOMALY_WINDOW)
        except Exception as e:
            log.debug(f"keenect: influx query failed for {zone_name}: {e}")
            continue
        if len(temps) < 4:
            continue

        # Compare first half avg vs second half avg
        mid = len(temps) // 2
        first_avg = sum([t[1] for t in temps[:mid]]) / mid
        second_avg = sum([t[1] for t in temps[mid:]]) / (len(temps) - mid)
        trend = round(second_avg - first_avg, 1)

        zone_display = zone_name.replace("_", " ").title()
        if mode == "HEAT" and trend < -ANOMALY_THRESHOLD:
            anomalies.append({
                "zone": zone_display,
                "issue": f"Temp dropping {abs(trend)}°F while heating",
                "current": round(current_temp, 1),
                "setpoint": int(setpoint),
                "trend": trend,
            })
        elif mode == "COOL" and trend > ANOMALY_THRESHOLD:
            anomalies.append({
                "zone": zone_display,
                "issue": f"Temp rising {trend}°F while cooling",
                "current": round(current_temp, 1),
                "setpoint": int(setpoint),
                "trend": trend,
            })

    return anomalies


@time_trigger("cron(*/10 * * * *)")
def check_anomalies():
    """Periodic anomaly detection using InfluxDB temperature trends."""
    try:
        anomalies = _detect_anomalies()
        if anomalies:
            summary = "; ".join([f"{a['zone']}: {a['issue']}" for a in anomalies])
        else:
            summary = "OK"
        state.set("sensor.keenect_anomalies", summary, {
            "friendly_name": "HVAC Anomalies",
            "icon": "mdi:alert-circle" if anomalies else "mdi:check-circle",
            "anomalies": anomalies,
        })
        if anomalies:
            for a in anomalies:
                log.warning(
                    f"keenect: ANOMALY - {a['zone']}: {a['issue']} "
                    f"(current={a['current']}°F, setpoint={a['setpoint']}°F)"
                )
            msg = "\n".join([
                f"**{a['zone']}**: {a['issue']} "
                f"(current {a['current']}°F, setpoint {a['setpoint']}°F)"
                for a in anomalies
            ])
            persistent_notification.create(
                title="HVAC Anomaly Detected",
                message=msg,
                notification_id="keenect_anomaly",
            )
        else:
            persistent_notification.dismiss(notification_id="keenect_anomaly")
    except Exception as e:
        log.error(f"keenect: check_anomalies crashed: {e}")


@time_trigger("period(now, 60s)")
def keenect_heartbeat():
    """Publish MQTT heartbeat with HVAC state for ESPHome sync on boot."""
    import subprocess
    payload = json_mod.dumps({
        "heat": _st.get("main_state") == "HEATING",
        "cool": _st.get("main_state") == "COOLING",
        "fan": _st.get("hvac_on", False) or _st.get("recirc_active", False) or _circ_enabled(),
    }, separators=(',', ':'))
    try:
        mqtt.publish(topic="keenect/heartbeat", payload=payload, qos=1, retain=True)
    except Exception:
        try:
            task.executor(subprocess.run,
                          ["mosquitto_pub", "-h", "192.168.1.10", "-t", "keenect/heartbeat",
                           "-m", payload, "-r", "-q", "1"],
                          timeout=5)
        except Exception as e:
            log.warning(f"keenect: heartbeat publish failed: {e}")


@state_trigger("switch.hvac_controller_bypass")
def on_bypass_change(**kwargs):
    """React immediately when ESP32 bypass switch is toggled on or off."""
    try:
        bypass_on = state.get("switch.hvac_controller_bypass") == "on"
        log.info(f"keenect: ESP32 bypass {'engaged' if bypass_on else 'cleared'}")
        # NOTE: after a brief bypass ends, last_hvac_off_time is unchanged so the
        # short-cycle cool guard (300s) may delay compressor restart. This is intentional
        # — bypassing and quickly resuming still counts as a shutoff for cycle protection.
        _eval_master()
    except Exception as e:
        log.error(f"keenect: on_bypass_change crashed: {e}")
