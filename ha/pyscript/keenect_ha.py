"""
Keenect HA - Keen Vent Zone Control for Home Assistant (pyscript)
Replaces Hubitat KeenectLiteMaster + KeenectLiteZone

Controls Keen smart vents via Hubitat integration (Zigbee radios on Hubitat).
HVAC furnace controlled directly via HTTP to Flask server (bypasses Hubitat).
First floor servo register controlled directly via HTTP (WiFi device).

Version: 1.3.0
"""

import json as json_mod
import time as time_mod
import urllib.request

# ---------------------------------------------------------------------------
# Zone configuration
# ---------------------------------------------------------------------------
ZONES = {
    "ben": {
        "thermostat": "climate.keen_ben_thermostat",
        "vents": ["cover.keen_ben"],
        "vent_type": "cover",
        "heat_min_vo": 0, "heat_max_vo": 100,
        "cool_min_vo": 0, "cool_max_vo": 100,
        "fan_vo": 30,
        "vent_control": "Normal",
        "exclude_recirc": False,
    },
    "gene": {
        "thermostat": "climate.keen_gene_thermostat",
        "vents": ["cover.keen_gene"],
        "vent_type": "cover",
        "heat_min_vo": 0, "heat_max_vo": 100,
        "cool_min_vo": 0, "cool_max_vo": 100,
        "fan_vo": 30,
        "vent_control": "Normal",
        "exclude_recirc": False,
    },
    "mbr": {
        "thermostat": "climate.keen_mbr_virtual_thermostat",
        "vents": ["cover.keen_mbr_1", "cover.keen_mbr_2"],
        "vent_type": "cover",
        "heat_min_vo": 0, "heat_max_vo": 100,
        "cool_min_vo": 0, "cool_max_vo": 100,
        "fan_vo": 30,
        "vent_control": "Normal",
        "exclude_recirc": False,
    },
    "first_floor": {
        "thermostat": "climate.first_floor_virtual_thermostat",
        "vents": ["servo"],  # WiFi servo register at SERVO_SERVER
        "vent_type": "servo",
        "heat_min_vo": 0, "heat_max_vo": 45,  # servo max angle is 45 degrees
        "cool_min_vo": 0, "cool_max_vo": 45,
        "fan_vo": 15,  # ~33% of 45
        "vent_control": "Normal",
        "exclude_recirc": False,
    },
}

# HVAC Flask server - direct HTTP control (bypasses Hubitat driver)
HVAC_SERVER = "http://192.168.1.123:5000"
# Servo register - WiFi device, POST /move?angle=N
SERVO_SERVER = "http://192.168.1.63"
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

OUTDOOR_TEMP_ENTITY = "sensor.outdoor_temperature"
STATE_ENTITY = "input_text.keenect_persisted_state"

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
    "retry_count": 0,
    "hvac_off_time": None,      # timestamp when HVAC was turned off
    "vents_closed_after_off": True,  # whether vents were closed after last HVAC off
    "_last_persisted": None,    # snapshot of last persisted state
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
    data = {
        "ms": _st["main_state"],
        "ho": 1 if _st["hvac_on"] else 0,
        "ra": 1 if _st["recirc_active"] else 0,
        "zs": {k: v[:1] for k, v in _st["zone_states"].items()},  # I/H/C/F
        "ot": _st["hvac_off_time"],
        "vc": 1 if _st["vents_closed_after_off"] else 0,
    }
    try:
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
    val = state.get(entity_id)
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


def _hysteresis():
    return _float("input_number.keenect_hysteresis", 0.5)


def _cool_lockout_temp():
    return _float("input_number.cool_lockout_temp", 50.0)


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
            urllib.request.urlopen(url, timeout=5)
            if attempt > 0:
                log.info(f"keenect: HVAC GET {url} (retry {attempt} ok)")
            else:
                log.info(f"keenect: HVAC GET {url}")
            return True
        except Exception as e:
            log.warning(f"keenect: HVAC {url} attempt {attempt+1} failed: {e}")
            if attempt < 2:
                time_mod.sleep(1)
    log.error(f"keenect: HVAC command {url} FAILED after 3 attempts")
    return False


def _outdoor_temp():
    return _float(OUTDOOR_TEMP_ENTITY)


def _hvac_turn_on():
    """Activate HVAC in current mode."""
    mode = _hvac_mode()
    if mode == "OFF":
        log.info("keenect: HVAC mode OFF, ignoring on request")
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
        ok = _hvac_push(2)   # heatOn
        _hvac_push(6)        # fanOn
        _st["main_state"] = "HEATING"
    elif mode == "COOL":
        ok = _hvac_push(4)   # coolOn
        _hvac_push(6)        # fanOn
        _st["main_state"] = "COOLING"
    else:
        return
    _st["hvac_on"] = True
    log.info(f"keenect: HVAC ON in {mode} mode")


def _hvac_turn_off():
    """Shut down HVAC with proper sequence."""
    log.info("keenect: HVAC shutdown")
    ms = _st["main_state"]
    if ms == "HEATING":
        _hvac_push(3)   # heatOff
    elif ms == "COOLING":
        _hvac_push(5)   # coolOff
    else:
        _hvac_push(1)   # general off

    if _st["recirc_active"] or _circ_enabled():
        _hvac_push(6)   # keep fan
        log.info("keenect: keeping fan on (recirc/circ)")
    else:
        _hvac_push(7)   # fan off

    _st["main_state"] = "IDLE"
    _st["hvac_on"] = False

    # Schedule delayed vent closure (checked in periodic eval)
    if not _st["recirc_active"]:
        _st["hvac_off_time"] = time_mod.time()
        _st["vents_closed_after_off"] = False
        delay = _vent_delay()
        log.info(f"keenect: vent closure in {delay}s (timer-based)")


# ---------------------------------------------------------------------------
# Vent control
# ---------------------------------------------------------------------------
def _set_vent(zone_name, level):
    """Set vent opening for a zone (0-100)."""
    zone = ZONES[zone_name]
    level = max(0, min(100, int(level)))
    vtype = zone.get("vent_type", "cover")

    for vent_id in zone["vents"]:
        key = f"{zone_name}:{vent_id}"
        current = _st["vent_levels"].get(key, -99)
        if abs(current - level) <= 4:
            continue

        try:
            if vtype == "servo":
                url = f"{SERVO_SERVER}/move?angle={level}"
                ok = False
                for attempt in range(3):
                    try:
                        req = urllib.request.Request(url, data=b"", method="POST")
                        urllib.request.urlopen(req, timeout=5)
                        ok = True
                        break
                    except Exception as e2:
                        log.warning(f"keenect: servo {url} attempt {attempt+1}: {e2}")
                        if attempt < 2:
                            time_mod.sleep(1)
                if not ok:
                    log.error(f"keenect: servo {url} FAILED after 3 attempts")
                    continue
            elif vtype == "light":
                if level == 0:
                    light.turn_off(entity_id=vent_id)
                else:
                    light.turn_on(entity_id=vent_id, brightness_pct=level)
            else:
                cover.set_cover_position(entity_id=vent_id, position=level)
            _st["vent_levels"][key] = level
            log.info(f"keenect: {zone_name} vent {vent_id} -> {level}%")
        except Exception as e:
            log.error(f"keenect: failed {vent_id} -> {level}: {e}")


def _close_zone(zone_name):
    _set_vent(zone_name, 0)


def _close_all_vents():
    for zn in ZONES:
        _close_zone(zn)


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
    return max(mn, min(mx, opening))


# ---------------------------------------------------------------------------
# Zone evaluation
# ---------------------------------------------------------------------------
def _get_climate_attr(entity_id, attr, default=None):
    """Read a climate entity attribute."""
    val = state.getattr(entity_id).get(attr)
    if val is None:
        return default
    try:
        return float(val) if isinstance(val, (int, float)) else val
    except (ValueError, TypeError):
        return default


def _eval_zone(zone_name):
    zone = ZONES[zone_name]
    tstat = zone["thermostat"]

    # Read from climate entity attributes (always available, no extra sensors needed)
    temp = _get_climate_attr(tstat, "current_temperature")
    heat_sp = _get_climate_attr(tstat, "temperature")  # target temp in heat mode
    cool_sp = _get_climate_attr(tstat, "target_temp_high")  # target in cool mode
    # If heat_cool mode, use target_temp_low for heat
    target_low = _get_climate_attr(tstat, "target_temp_low")
    if target_low is not None:
        heat_sp = target_low
    op_raw = _get_climate_attr(tstat, "hvac_action")

    if temp is None:
        return

    # Keenect offsets: heat_sp + 1, cool_sp - 1
    zheat = (float(heat_sp) + 1) if heat_sp is not None else None
    zcool = (float(cool_sp) - 1) if cool_sp is not None else None

    op = (str(op_raw) if op_raw else "idle").upper()
    if op not in ("HEATING", "COOLING", "FAN ONLY", "IDLE"):
        op = "IDLE"

    old = _st["zone_states"].get(zone_name, "IDLE")

    # Hysteresis override
    hyst = _hysteresis()
    if op == "HEATING" and zheat is not None and temp >= zheat + hyst:
        op = "IDLE"
    if op == "COOLING" and zcool is not None and temp <= zcool - hyst:
        op = "IDLE"

    _st["zone_states"][zone_name] = op

    if op in ("HEATING", "COOLING", "FAN ONLY"):
        sp = zheat if op == "HEATING" else zcool
        if sp is None:
            sp = temp
        opening = _calc_opening(zone_name, op, temp, sp)
        _set_vent(zone_name, opening)
    elif op == "IDLE" and old != "IDLE":
        # Zone just went idle
        others_active = any(
            s not in ("IDLE", "OFF", "")
            for n, s in _st["zone_states"].items() if n != zone_name
        )
        if others_active:
            log.info(f"keenect: {zone_name} idle, others active -> close vents")
            _close_zone(zone_name)
        else:
            log.info(f"keenect: {zone_name} idle (last zone), delayed closure")

    if op != old:
        log.info(f"keenect: {zone_name} {old}->{op} temp={temp} hsp={zheat} csp={zcool}")


def _all_idle():
    return all(s in ("IDLE", "OFF", "") for s in _st["zone_states"].values())


# ---------------------------------------------------------------------------
# Master evaluation
# ---------------------------------------------------------------------------
def _eval_master():
    if not _enabled():
        return

    now = time_mod.time()
    if now < _st["debounce_until"]:
        return
    _st["debounce_until"] = now + 2

    for zn in ZONES:
        _eval_zone(zn)

    demanding = sum(1 for s in _st["zone_states"].values() if s in ("HEATING", "COOLING"))
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
    if _st["recirc_active"]:
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
        if not zone.get("exclude_recirc"):
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
            "cool_lockout": ot is not None and ot < _cool_lockout_temp() if ot else False,
        })
    except Exception as e:
        log.warning(f"keenect: status update failed: {e}")
        return

    # Per-zone vent levels
    for zn, zone in ZONES.items():
        zstate = _st["zone_states"].get(zn, "IDLE")
        # Get current vent level from tracked state
        level = 0
        for vent_id in zone["vents"]:
            key = f"{zn}:{vent_id}"
            level = max(level, _st["vent_levels"].get(key, 0))

        if zone.get("vent_type") == "servo":
            name = f"Keenect {zn.replace('_', ' ').title()} Servo"
            unit = "°"
            icon = "mdi:rotate-right"
        else:
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
# Pyscript triggers
# ---------------------------------------------------------------------------

@time_trigger("startup")
def on_startup():
    """Restore persisted state and re-evaluate after HA restart."""
    _load_state()
    log.info(
        f"keenect: startup - hvac_on={_st['hvac_on']} "
        f"main={_st['main_state']} recirc={_st['recirc_active']}"
    )
    _update_status()
    if _enabled():
        _eval_master()
        _check_consistency()


@time_trigger("period(now, 15s)")
def periodic_eval():
    """Evaluate every 15 seconds (matches Hubitat schedule)."""
    _eval_master()


@state_trigger(
    "climate.keen_ben_thermostat",
    "climate.keen_gene_thermostat",
    "climate.keen_mbr_virtual_thermostat",
    "climate.first_floor_virtual_thermostat",
)
def on_climate_change(**kwargs):
    """React to any climate entity changes (state, temp, setpoint, hvac_action)."""
    log.info(f"keenect: climate change {kwargs.get('var_name')}")
    _eval_master()


@state_trigger("input_select.hvac_mode")
def on_mode_change(**kwargs):
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


@state_trigger("input_boolean.keenect_enabled")
def on_enable_change(**kwargs):
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


@time_trigger("cron(*/5 * * * *)")
def periodic_consistency():
    _check_consistency()


@time_trigger("cron(*/3 * * * *)")
def periodic_recirc():
    _check_recirc()


@time_trigger("cron(0 * * * *)")
def log_stats():
    log.info(
        f"keenect stats: state={_st['main_state']} hvac={_st['hvac_on']} "
        f"recirc={_st['recirc_active']} zones={_st['zone_states']} "
        f"vents={_st['vent_levels']}"
    )
