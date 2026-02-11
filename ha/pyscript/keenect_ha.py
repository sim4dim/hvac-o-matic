"""
Keenect HA - Keen Vent Zone Control for Home Assistant (pyscript)
Replaces Hubitat KeenectLiteMaster + KeenectLiteZone

Controls Keen smart vents and HVAC furnace through the Hubitat integration.
Vents are Zigbee devices on Hubitat; commands go via hubitat.send_command.
HVAC furnace is controlled via HTTP push buttons on the Hubitat HVAC driver.

Version: 1.0.0
"""

import time as time_mod

# ---------------------------------------------------------------------------
# Zone configuration
# ---------------------------------------------------------------------------
ZONES = {
    "ben": {
        "thermostat": "climate.keen_ben_thermostat",
        "vents": ["cover.keen_ben"],
        "vent_type": "cover",
        "temp_sensor": "sensor.keen_ben_thermostat_temperature",
        "heat_sp_sensor": "sensor.keen_ben_thermostat_heatingsetpoint",
        "cool_sp_sensor": "sensor.keen_ben_thermostat_coolingsetpoint",
        "op_state_sensor": "sensor.keen_ben_thermostat_thermostatoperatingstate",
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
        "temp_sensor": "sensor.keen_gene_thermostat_temperature",
        "heat_sp_sensor": "sensor.keen_gene_thermostat_heatingsetpoint",
        "cool_sp_sensor": "sensor.keen_gene_thermostat_coolingsetpoint",
        "op_state_sensor": "sensor.keen_gene_thermostat_thermostatoperatingstate",
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
        "temp_sensor": "sensor.keen_mbr_virtual_thermostat_temperature",
        "heat_sp_sensor": "sensor.keen_mbr_virtual_thermostat_heatingsetpoint",
        "cool_sp_sensor": "sensor.keen_mbr_virtual_thermostat_coolingsetpoint",
        "op_state_sensor": "sensor.keen_mbr_virtual_thermostat_thermostatoperatingstate",
        "heat_min_vo": 0, "heat_max_vo": 100,
        "cool_min_vo": 0, "cool_max_vo": 100,
        "fan_vo": 30,
        "vent_control": "Normal",
        "exclude_recirc": False,
    },
    "first_floor": {
        "thermostat": "climate.first_floor_virtual_thermostat",
        "vents": ["light.first_floor_register"],
        "vent_type": "light",  # servo register controlled via brightness
        "temp_sensor": "sensor.first_floor_virtual_thermostat_temperature",
        "heat_sp_sensor": "sensor.first_floor_virtual_thermostat_heatingsetpoint",
        "cool_sp_sensor": "sensor.first_floor_virtual_thermostat_coolingsetpoint",
        "op_state_sensor": "sensor.first_floor_virtual_thermostat_thermostatoperatingstate",
        "heat_min_vo": 0, "heat_max_vo": 100,
        "cool_min_vo": 0, "cool_max_vo": 100,
        "fan_vo": 30,
        "vent_control": "Normal",
        "exclude_recirc": False,
    },
}

# HVAC driver entity - used for hubitat.send_command push buttons
# Button map: 1=off, 2=heatOn, 3=heatOff, 4=coolOn, 5=coolOff, 6=fanOn, 7=fanOff
HVAC_ENTITY = "event.first_floor_register_button_1"

HYSTERESIS = 0.5

# ---------------------------------------------------------------------------
# Module-level state (resets on pyscript reload)
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
}


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


# ---------------------------------------------------------------------------
# HVAC furnace control
# ---------------------------------------------------------------------------
def _hvac_push(button):
    """Push a button on the HVAC driver via Hubitat."""
    log.info(f"keenect: HVAC push button {button}")
    hubitat.send_command(entity_id=HVAC_ENTITY, command="push", args=button)


def _hvac_turn_on():
    """Activate HVAC in current mode."""
    mode = _hvac_mode()
    if mode == "OFF":
        log.info("keenect: HVAC mode OFF, ignoring on request")
        return
    # Cancel any pending vent closure
    _st["hvac_off_time"] = None
    _st["vents_closed_after_off"] = True

    if mode == "HEAT":
        _hvac_push(2)   # heatOn
        _hvac_push(6)   # fanOn
        _st["main_state"] = "HEATING"
    elif mode == "COOL":
        _hvac_push(4)   # coolOn
        _hvac_push(6)   # fanOn
        _st["main_state"] = "COOLING"
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
            if vtype == "light":
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
def _eval_zone(zone_name):
    zone = ZONES[zone_name]
    temp = _float(zone["temp_sensor"])
    heat_sp = _float(zone["heat_sp_sensor"])
    cool_sp = _float(zone["cool_sp_sensor"])
    op_raw = state.get(zone["op_state_sensor"])

    if temp is None:
        return

    # Keenect offsets: heat_sp + 1, cool_sp - 1
    zheat = (heat_sp + 1) if heat_sp is not None else None
    zcool = (cool_sp - 1) if cool_sp is not None else None

    op = (op_raw or "idle").upper()
    if op not in ("HEATING", "COOLING", "FAN ONLY", "IDLE"):
        op = "IDLE"

    old = _st["zone_states"].get(zone_name, "IDLE")

    # Hysteresis override
    if op == "HEATING" and zheat is not None and temp >= zheat + HYSTERESIS:
        op = "IDLE"
    if op == "COOLING" and zcool is not None and temp <= zcool - HYSTERESIS:
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


# ---------------------------------------------------------------------------
# Pyscript triggers
# ---------------------------------------------------------------------------

@time_trigger("period(now, 15s)")
def periodic_eval():
    """Evaluate every 15 seconds (matches Hubitat schedule)."""
    _eval_master()


@state_trigger(
    "sensor.keen_ben_thermostat_thermostatoperatingstate",
    "sensor.keen_gene_thermostat_thermostatoperatingstate",
    "sensor.keen_mbr_virtual_thermostat_thermostatoperatingstate",
    "sensor.first_floor_virtual_thermostat_thermostatoperatingstate",
)
def on_op_state_change(**kwargs):
    """React to thermostat operating state changes."""
    log.info(f"keenect: thermostat state change {kwargs.get('var_name')}")
    _eval_master()


@state_trigger(
    "sensor.keen_ben_thermostat_temperature",
    "sensor.keen_gene_thermostat_temperature",
    "sensor.keen_mbr_virtual_thermostat_temperature",
    "sensor.first_floor_virtual_thermostat_temperature",
)
def on_temp_change(**kwargs):
    _eval_master()


@state_trigger(
    "sensor.keen_ben_thermostat_heatingsetpoint",
    "sensor.keen_ben_thermostat_coolingsetpoint",
    "sensor.keen_gene_thermostat_heatingsetpoint",
    "sensor.keen_gene_thermostat_coolingsetpoint",
    "sensor.keen_mbr_virtual_thermostat_heatingsetpoint",
    "sensor.keen_mbr_virtual_thermostat_coolingsetpoint",
    "sensor.first_floor_virtual_thermostat_heatingsetpoint",
    "sensor.first_floor_virtual_thermostat_coolingsetpoint",
)
def on_setpoint_change(**kwargs):
    log.info(f"keenect: setpoint change {kwargs.get('var_name')}")
    _eval_master()


@state_trigger("input_select.hvac_mode")
def on_mode_change(**kwargs):
    mode = state.get("input_select.hvac_mode")
    log.info(f"keenect: HVAC mode -> {mode}")
    if mode == "OFF":
        if _st["hvac_on"]:
            _hvac_turn_off()
        _close_all_vents()
    else:
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
