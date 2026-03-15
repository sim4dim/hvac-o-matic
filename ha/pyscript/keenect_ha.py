"""
Keenect HA - Keen Vent Zone Control for Home Assistant (pyscript)
Replaces Hubitat KeenectLiteMaster + KeenectLiteZone

Controls Keen smart vents via Hubitat integration (Zigbee radios on Hubitat).
HVAC furnace controlled directly via HTTP to Flask server (bypasses Hubitat).
First floor servo register controlled via ESPHome native API (number entity).

Version: 2.5.0
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
        health_sensors = []
        for v in vents:
            if vent_type == "light":
                vent_suffix = v.replace("light.", "")
                health_entity = f"sensor.{vent_suffix}_pressure"
                if health_entity in all_sensor_names:
                    health_sensors.append(health_entity)

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

OUTDOOR_TEMP_ENTITY = "sensor.outdoor_temperature"

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

# Zone name <-> compact code for setpoint log persistence (255 char limit)
# Populated dynamically by _build_derived_maps() at startup; static values are fallback
_ZONE_TO_CODE = {"Ben": "b", "Gene": "g", "Mbr": "m", "First Floor": "f"}
_CODE_TO_ZONE = {"b": "Ben", "g": "Gene", "m": "Mbr", "f": "First Floor"}

# Vent health check - if pressure sensor hasn't reported in this many seconds,
# the vent is likely offline. Pressure reports every ~5 min, so 30 min = very stale.
VENT_STALE_SECONDS = 1800  # 30 minutes

# Keys persisted via input_text (survive HA restarts)
_PERSIST_KEYS = [
    "main_state", "hvac_on", "recirc_active", "zone_states",
    "hvac_off_time", "vents_closed_after_off",
]

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
    # Safety: short-cycle and min-run protection timestamps
    "last_hvac_on_time": 0,
    "last_hvac_off_time": 0,
    # Warmup sensor health flag
    "_warmup_sensors_ok": True,
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
        "ot": _st["hvac_off_time"],
        "vc": 1 if _st["vents_closed_after_off"] else 0,
        "sv": sv,
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


def _circ_enabled():
    return state.get("input_boolean.enable_circulation") == "on"


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
    """Send HVAC command directly to Flask server with retry."""
    path = HVAC_COMMANDS.get(button)
    if path is None:
        log.error(f"keenect: unknown HVAC button {button}")
        return False
    url = f"{HVAC_SERVER}/0{path}"
    for attempt in range(3):
        try:
            task.executor(urllib.request.urlopen, url, None, 5)
            if attempt > 0:
                log.info(f"keenect: HVAC GET {url} (retry {attempt} ok)")
            else:
                log.info(f"keenect: HVAC GET {url}")
            _hvac_esphome_mirror(button)
            _st["push_fail_count"] = 0
            return True
        except Exception as e:
            log.warning(f"keenect: HVAC {url} attempt {attempt+1} failed: {e}")
            if attempt < 2:
                task.sleep(1)
    log.error(f"keenect: HVAC command {url} FAILED after 3 attempts")
    _st["push_fail_count"] = _st.get("push_fail_count", 0) + 1
    fc = _st["push_fail_count"]
    if fc >= 5:
        log.error(f"keenect: {fc} consecutive push failures, disabling keenect")
        try:
            persistent_notification.create(
                title="Keenect: HVAC Push CRITICAL",
                message=f"{fc} consecutive HVAC push commands failed. "
                        f"Keenect has been disabled. Check Flask server at {HVAC_SERVER}.",
                notification_id="keenect_push_fail",
            )
        except Exception:
            pass
        try:
            input_boolean.turn_off(entity_id="input_boolean.keenect_enabled")
        except Exception as e2:
            log.error(f"keenect: failed to disable keenect: {e2}")
    elif fc >= 3:
        log.warning(f"keenect: {fc} consecutive push failures")
        try:
            persistent_notification.create(
                title="Keenect: HVAC Push Failures",
                message=f"{fc} consecutive HVAC push commands failed. "
                        f"Check Flask server at {HVAC_SERVER}.",
                notification_id="keenect_push_fail",
            )
        except Exception:
            pass
    return False


def _hvac_esphome_mirror(button):
    """Send relay commands to ESPHome test controller via HA switches."""
    actions = ESPHOME_MIRROR_MAP.get(button)
    if not actions:
        return
    for action, entity in actions:
        try:
            if action == "turn_on":
                switch.turn_on(entity_id=entity)
            else:
                switch.turn_off(entity_id=entity)
        except Exception as e:
            log.warning(f"keenect: ESPHome mirror {entity} failed: {e}")


def _outdoor_temp():
    return _float(OUTDOOR_TEMP_ENTITY)


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
        if ot is not None and ot < lockout:
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

    if mode == "HEAT":
        ok1 = _hvac_push(2)        # heatOn
        ok2 = _hvac_push(6)        # fanOn
        if not ok1 or not ok2:
            log.error("keenect: HVAC HEAT on push failed, not updating state")
            return
        _st["main_state"] = "HEATING"
    elif mode == "COOL":
        ok1 = _hvac_push(4)        # coolOn
        ok2 = _hvac_push(6)        # fanOn
        if not ok1 or not ok2:
            log.error("keenect: HVAC COOL on push failed, not updating state")
            return
        _st["main_state"] = "COOLING"
    else:
        return
    _st["hvac_on"] = True
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
        if level > 0 and abs(current - level) <= 4:
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
    """Check Keen light vents actually reached their target; clear cache to retry if not."""
    for zn, zone in ZONES.items():
        if zone.get("vent_type") != "light":
            continue
        for vent_id in zone["vents"]:
            key = f"{zn}:{vent_id}"
            target = _st["vent_levels"].get(key)
            if target is None:
                continue
            try:
                s = state.get(vent_id)
                if target == 0 and s == "off":
                    continue  # correct
                if target > 0 and s == "on":
                    attrs = state.getattr(vent_id)
                    bri = int(attrs.get("brightness", 0))
                    actual_pct = round(bri * 100 / 255) if bri else 0
                    if abs(actual_pct - target) <= 10:
                        continue  # close enough
                # Mismatch - clear cache so next eval retries
                log.warning(f"keenect: vent {vent_id} target={target} actual={s}, clearing cache to retry")
                del _st["vent_levels"][key]
            except Exception as e:
                log.debug(f"keenect: verify vent {vent_id}: {e}")


# ---------------------------------------------------------------------------
# Vent opening calculation
# ---------------------------------------------------------------------------
def _calc_opening(zone_name, zstate, temp, setpoint):
    """Proportional vent opening based on temp delta."""
    zone = ZONES[zone_name]
    ctrl = zone["vent_control"]

    if zstate == "HEATING":
        delta = setpoint - temp
        mn, mx = zone["heat_min_vo"], zone["heat_max_vo"]
    elif zstate == "COOLING":
        delta = temp - setpoint
        mn, mx = zone["cool_min_vo"], zone["cool_max_vo"]
    elif zstate == "FAN ONLY":
        return zone["fan_vo"]
    else:
        return 0

    rng = mx - mn
    if ctrl == "Aggressive":
        slope, intercept = rng * 2, rng / 5 + mn
    elif ctrl == "Slow":
        slope, intercept = rng / 2, mn
    elif ctrl == "Binary":
        slope, intercept = 10000, mn
    else:  # Normal
        slope, intercept = rng, mn

    opening = round(delta * slope + intercept)

    # Apply zone learning factor (slow zones get boosted, fast zones reduced)
    factors = _st.get("zone_vent_factors", {}).get(zone_name, {})
    if zstate == "HEATING":
        factor = factors.get("heat_factor", 1.0)
    else:
        factor = factors.get("cool_factor", 1.0)
    if factor != 1.0:
        opening = round(opening * factor)

    return max(mn, min(mx, opening))


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
            # During warmup, cap vents at minimum to avoid blowing unconditioned air
            if warming_up and op in ("HEATING", "COOLING"):
                zone = ZONES[zone_name]
                min_vo = zone["heat_min_vo"] if op == "HEATING" else zone["cool_min_vo"]
                if opening > min_vo:
                    opening = min_vo
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
        ac_kw = _float("input_number.ac_wattage", 3500) / 1000.0
        price = _float("sensor.current_electric_rate", 0.20)
        cost = ac_kw * elapsed_h * price
        _st["cool_runtime"] += elapsed_h
        _st["cool_cost"] += cost
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
    state.set("sensor.hvac_cool_cost", round(_st["cool_cost"], 2), {
        "unit_of_measurement": "$",
        "state_class": "total_increasing",
        "icon": "mdi:currency-usd",
        "friendly_name": "Cooling Cost",
    })


# ---------------------------------------------------------------------------
# Master evaluation
# ---------------------------------------------------------------------------
def _eval_master():
    if not _enabled():
        _update_status()
        return

    now = time_mod.time()
    if now < _st["debounce_until"]:
        return
    _st["debounce_until"] = now + 2

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

    # Emergency: ALL zones returning None while HVAC is on
    if len(none_zones) == len(ZONES) and _st.get("hvac_on"):
        log.error("keenect: EMERGENCY - ALL zone sensors returning None while HVAC on, forcing off")
        # Direct push commands — bypass normal _hvac_turn_off checks
        _hvac_push(3)   # heatOff
        _hvac_push(5)   # coolOff
        _hvac_push(7)   # fanOff
        _st["main_state"] = "IDLE"
        _st["hvac_on"] = False
        _st["warmup_start"] = None
        _st["last_hvac_off_time"] = time_mod.time()
        try:
            persistent_notification.create(
                title="Keenect: EMERGENCY SHUTOFF",
                message="ALL zone temperature sensors returned None simultaneously. "
                        "HVAC has been force-shut-off. Check sensor connectivity immediately.",
                notification_id="keenect_emergency_shutoff",
            )
        except Exception:
            pass
        _save_if_changed()
        return

    # Pass 2: apply vent actions with complete picture (order-independent)
    _apply_zone_vents(results)

    demanding = sum([1 for r in results.values() if r["op"] in ("HEATING", "COOLING")])
    mode = _hvac_mode()

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
    else:
        status = _st["main_state"]
    ot = _outdoor_temp()
    try:
        state.set("sensor.keenect_status", status, {
            "friendly_name": "Keenect Status",
            "icon": "mdi:hvac",
            "hvac_on": _st["hvac_on"],
            "recirc_active": _st["recirc_active"],
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
                        attrs = state.getattr(vent_id)
                        bri = attrs.get("brightness", 0)
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
def _check_vent_health():
    """Detect stale Keen vents and re-send last commanded position."""
    if not _enabled():
        return

    utc_now = dt_mod.datetime.now(dt_mod.timezone.utc)
    stale_vents = []

    for zn, zone in ZONES.items():
        health_sensors = zone.get("health_sensors", [])
        if not health_sensors:
            continue

        for i, sensor_id in enumerate(health_sensors):
            vent_id = zone["vents"][min(i, len(zone["vents"]) - 1)]
            try:
                s = state.get(sensor_id)

                # Check if outright unavailable
                if s in ("unavailable", "unknown", None):
                    stale_vents.append((zn, vent_id, sensor_id, "unavailable"))
                    continue

                # Check staleness via last_updated timestamp
                attrs = state.getattr(sensor_id)
                last_updated = attrs.get("last_updated")
                if last_updated is None:
                    continue

                dt = dt_mod.datetime.fromisoformat(str(last_updated))
                age = (utc_now - dt).total_seconds()
                if age > VENT_STALE_SECONDS:
                    stale_vents.append((zn, vent_id, sensor_id,
                                       f"stale {int(age / 60)}m"))
            except Exception as e:
                log.debug(f"keenect: health check error {sensor_id}: {e}")

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

    # Persistent notification in HA
    vent_list = ", ".join([f"{vid} ({reason})" for _, vid, _, reason in stale_vents])
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
        was_on = _st["hvac_on"]
        _st["hvac_on"] = False
        _st["main_state"] = "IDLE"
        _st["warmup_start"] = None
        _st["vent_levels"] = {}  # force vent re-sends

        # P6: Hardware safety sync FIRST — ensure furnace is off before anything else.
        # This prevents the furnace from running uncontrolled during startup logic.
        log.info("keenect: startup - hardware safety sync: ensuring furnace off")
        _hvac_push(3)  # heatOff
        _hvac_push(5)  # coolOff
        if not _circ_enabled():
            _hvac_push(7)  # fanOff
        safety_synced = True

        if was_on:
            log.info("keenect: startup - was_on=True, will re-arm via eval")
        log.info(f"keenect: startup - restored zone_states={_st['zone_states']}")
        _restore_setpoint_log()
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
        # Apply away setpoints for zones already in away mode
        away_heat = _float("input_number.away_heat_setpoint", 55.0)
        away_cool = _float("input_number.away_cool_setpoint", 85.0)
        for zn in ZONES:
            if state.get(f"input_boolean.away_{zn}") == "on":
                cur_heat = _float(f"input_number.{zn}_heat_setpoint")
                cur_cool = _float(f"input_number.{zn}_cool_setpoint")
                if cur_heat is not None and (cur_heat != away_heat or cur_cool != away_cool):
                    _st.setdefault("saved_setpoints", {})[zn] = {
                        "heat": cur_heat,
                        "cool": cur_cool,
                    }
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
            # (state_trigger won't fire since boolean was already on before reload)
            if _circ_enabled():
                log.info("keenect: startup - circulation active, opening vents + fan")
                for zn, zone in ZONES.items():
                    if not _zone_circ_excluded(zn):
                        _set_vent(zn, zone.get("fan_vo", 30))
                _hvac_push(6)  # fanOn
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


@state_trigger("input_boolean.enable_circulation")
def on_circ_change(**kwargs):
    """React to continuous circulation toggle."""
    try:
        on = _circ_enabled()
        log.info(f"keenect: continuous circulation {'ON' if on else 'OFF'}")
        if on:
            # Open non-excluded vents to fan position and turn fan on
            for zn, zone in ZONES.items():
                if not _zone_circ_excluded(zn):
                    _set_vent(zn, zone.get("fan_vo", 30))
            _hvac_push(6)  # fanOn
        else:
            # Turn fan off and close vents (unless HVAC or recirc is active)
            if not _st["hvac_on"] and not _st["recirc_active"]:
                _hvac_push(7)  # fanOff
                if _all_idle():
                    _close_all_vents()
        _update_status()
        _save_if_changed()
    except Exception as e:
        log.error(f"keenect: on_circ_change crashed: {e}")


@state_trigger("input_boolean.circ_optout_ben", "input_boolean.circ_optout_gene",
               "input_boolean.circ_optout_mbr", "input_boolean.circ_optout_first_floor")
def on_circ_optout_change(**kwargs):
    """React to per-zone circulation opt-out toggle."""
    try:
        if not _circ_enabled() and not _st["recirc_active"]:
            return  # nothing to do if circulation isn't running
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
    """Track who changed a thermostat setpoint."""
    try:
        eid = kwargs.get("var_name", "")
        mapping = _SETPOINT_MAP.get(eid)
        if not mapping:
            return
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


@state_trigger(
    "input_boolean.away_ben", "input_boolean.away_gene",
    "input_boolean.away_mbr", "input_boolean.away_first_floor",
)
def on_away_change(**kwargs):
    """Update thermostat setpoints when away mode is toggled."""
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
            away_heat = _float("input_number.away_heat_setpoint", 55.0)
            away_cool = _float("input_number.away_cool_setpoint", 85.0)
            input_number.set_value(entity_id=heat_key, value=away_heat)
            input_number.set_value(entity_id=cool_key, value=away_cool)
            log.info(f"keenect: {zone_name} away ON — setpoints → {away_heat}/{away_cool}")
        elif new_val == "off":
            # Restore saved setpoints or zone defaults
            saved = _st.get("saved_setpoints", {}).get(zone_name)
            defaults = ZONE_DEFAULTS.get(zone_name, {"heat": 62, "cool": 76})
            heat_restore = saved["heat"] if saved and saved.get("heat") is not None else defaults["heat"]
            cool_restore = saved["cool"] if saved and saved.get("cool") is not None else defaults["cool"]
            input_number.set_value(entity_id=heat_key, value=heat_restore)
            input_number.set_value(entity_id=cool_key, value=cool_restore)
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
    outdoor_temps = _influx_series("outdoor_temperature", days=ZONE_LEARN_DAYS, interval_min=30)
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
    """Publish MQTT heartbeat for ESPHome watchdog."""
    import subprocess
    try:
        mqtt.publish(topic="keenect/heartbeat", payload="alive", qos=0)
    except Exception:
        try:
            task.executor(subprocess.run,
                          ["mosquitto_pub", "-h", "192.168.1.10", "-t", "keenect/heartbeat", "-m", "alive"],
                          timeout=5)
        except Exception as e:
            log.warning(f"keenect: heartbeat publish failed: {e}")
