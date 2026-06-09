"""
Irrigation Advisor — pyscript for Home Assistant
ET (Hargreaves-Samani) soil-water-balance for lawn + heat-stress counter for planters.
Advice-only: no sprinkler control.

Version: 1.0.0
"""

import datetime as dt_mod
import json as json_mod
import math as math_mod

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Grand Rapids MI area
LAT_DEG = 42.9701
LON_DEG = -85.5567
ELEV_FT  = 830

LAT_RAD = LAT_DEG * math_mod.pi / 180.0

# Lawn parameters (clay, cool-season turf)
KC_LAWN   = 0.80   # crop coefficient
DEFICIT_MAX    = 2.0   # field capacity ceiling (in)
DEFICIT_TRIGGER = 0.375  # MAD 50% of 0.75 in available → water trigger (in)
TARGET_APPLICATION = 1.0  # target application per watering (in)
DEFAULT_RATE = 0.4  # in/hr fallback if entity missing

# Forecast rain skip threshold (in)
FORECAST_SKIP_THRESHOLD = 0.4

# Entities
WEATHER_ENTITY   = "weather.forecast_home"
RAIN_TOTAL_ENTITY = "sensor.weewx_rain_total"
RAIN_WEEK_ENTITY  = "sensor.rain_this_week"
HUMIDITY_ENTITY   = "sensor.weewx_outdoor_humidity"
RATE_ENTITY       = "input_number.irrigation_sprinkler_rate"
LAWN_WATERED_BOOL = "input_boolean.irrigation_lawn_watered"
PLANTER_WATERED_BOOL = "input_boolean.irrigation_planters_watered"
STATE_ENTITY      = "input_text.irrigation_state"

# Output sensors
SENS_LAWN_ADVICE   = "sensor.irrigation_lawn_advice"
SENS_LAWN_DEFICIT  = "sensor.irrigation_lawn_deficit"
SENS_PLANTER_ADVICE = "sensor.irrigation_planter_advice"
SENS_ET0_TODAY     = "sensor.irrigation_et0_today"
SENS_LAWN_RUNTIME  = "sensor.irrigation_lawn_runtime"
SENS_FCST_RAIN_48H = "sensor.irrigation_forecast_rain_48h"

# Notify — use iTelephone (main phone) for morning alerts
# All 7 notify services found: elena_s_iphone_3, gt_p5110, ipad_air_5th_gen,
# itelephone, kitchen_ipad_air, ky6vl22c5w, macprom1
NOTIFY_SERVICE = "notify.itelephone"

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------
_st = {
    "lawn_deficit": 0.0,        # ld
    "last_rain_total": None,    # rt — bootstrap on first run
    "planter_heat_days": 0.0,   # ph
    "lawn_last_watered": None,  # ll
    "planter_last_watered": None,  # pl
}


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------
def _save_state():
    """Persist model state to input_text.irrigation_state (JSON, <255 chars)."""
    data = {
        "ld": round(_st["lawn_deficit"], 4),
        "rt": round(_st["last_rain_total"], 4) if _st["last_rain_total"] is not None else None,
        "ph": round(_st["planter_heat_days"], 2),
        "ll": _st["lawn_last_watered"],
        "pl": _st["planter_last_watered"],
    }
    try:
        val = json_mod.dumps(data, separators=(",", ":"))
        if len(val) > 255:
            # Safety truncate — drop last_watered strings
            data["ll"] = None
            data["pl"] = None
            val = json_mod.dumps(data, separators=(",", ":"))
        input_text.set_value(entity_id=STATE_ENTITY, value=val)
        log.debug(f"irrigation: saved state {val}")
    except Exception as e:
        log.error(f"irrigation: failed to save state: {e}")


def _load_state():
    """Load model state from input_text.irrigation_state."""
    try:
        raw = state.get(STATE_ENTITY)
        if raw in (None, "", "{}", "unknown", "unavailable"):
            log.info("irrigation: no saved state (first run)")
            return
        data = json_mod.loads(raw)
        _st["lawn_deficit"]         = float(data.get("ld", 0.0))
        _st["last_rain_total"]      = data.get("rt")  # may be None
        _st["planter_heat_days"]    = float(data.get("ph", 0.0))
        _st["lawn_last_watered"]    = data.get("ll")
        _st["planter_last_watered"] = data.get("pl")
        log.info(
            f"irrigation: loaded state — deficit={_st['lawn_deficit']:.3f}\" "
            f"rain_total={_st['last_rain_total']} "
            f"heat_days={_st['planter_heat_days']:.1f}"
        )
    except Exception as e:
        log.warning(f"irrigation: failed to load state: {e}")


# ---------------------------------------------------------------------------
# ET0 — Hargreaves-Samani (daily, FAO-56 Ra)
# ---------------------------------------------------------------------------
def _compute_et0(tmax_f, tmin_f, rh_mean, day_of_year):
    """
    Compute ET0 in inches/day using Hargreaves-Samani with humidity correction.

    tmax_f, tmin_f : daily high/low in °F
    rh_mean        : mean relative humidity (%)
    day_of_year    : 1-365 Julian day
    """
    # Convert to Celsius
    tmax_c = (tmax_f - 32.0) * 5.0 / 9.0
    tmin_c = (tmin_f - 32.0) * 5.0 / 9.0
    tmean_c = (tmax_c + tmin_c) / 2.0
    td_c = max(tmax_c - tmin_c, 0.0)

    J = day_of_year
    phi = LAT_RAD

    # FAO-56 extraterrestrial radiation (Ra) in MJ/m²/day
    dr = 1.0 + 0.033 * math_mod.cos(2.0 * math_mod.pi * J / 365.0)
    delta = 0.409 * math_mod.sin(2.0 * math_mod.pi * J / 365.0 - 1.39)
    omegas = math_mod.acos(-math_mod.tan(phi) * math_mod.tan(delta))
    Ra = (24.0 * 60.0 / math_mod.pi) * 0.0820 * dr * (
        omegas * math_mod.sin(phi) * math_mod.sin(delta)
        + math_mod.cos(phi) * math_mod.cos(delta) * math_mod.sin(omegas)
    )

    # Convert Ra from MJ/m²/day to mm/day equivalent (÷ 2.45, latent heat of vaporization)
    ra_mm = Ra * 0.408

    # Hargreaves-Samani ET0 in mm/day
    et0_mm = 0.0023 * ra_mm * (tmean_c + 17.8) * math_mod.sqrt(td_c)
    # Convert to inches/day
    et0_in = et0_mm / 25.4

    # Humidity correction (humid MI climate — prevent over-irrigation)
    rh_correction = max(0.7, min(1.0, 1.0 - 0.004 * (rh_mean - 45.0)))
    et0_in = et0_in * rh_correction

    return max(0.0, et0_in)


# ---------------------------------------------------------------------------
# Effective rain (clay runoff heuristic)
# ---------------------------------------------------------------------------
def _effective_rain(rain_in):
    """Return effective rain for clay soil given raw daily rain total."""
    if rain_in < 0.5:
        return rain_in * 1.0
    elif rain_in <= 1.0:
        return rain_in * 0.8
    else:
        return 0.5  # maximum effective on heavy clay


# ---------------------------------------------------------------------------
# Forecast helpers
# ---------------------------------------------------------------------------
def _get_daily_forecasts():
    """
    Call weather.get_forecasts and return list of daily forecast dicts.
    Returns [] on failure.
    """
    try:
        resp = weather.get_forecasts(entity_id=WEATHER_ENTITY, forecast_type="daily",
                                     return_response=True)
        # resp is a dict: {entity_id: {"forecast": [...]}}
        if resp and WEATHER_ENTITY in resp:
            return resp[WEATHER_ENTITY].get("forecast", [])
        return []
    except Exception as e:
        log.warning(f"irrigation: failed to get forecasts: {e}")
        return []


def _get_today_temps_from_forecast(forecasts):
    """Extract today's Tmax (temperature) and Tmin (templow) from forecast list."""
    if not forecasts:
        return None, None
    today_fc = forecasts[0]
    tmax = today_fc.get("temperature")
    tmin = today_fc.get("templow")
    return tmax, tmin


def _get_forecast_precip_48h(forecasts):
    """Sum precipitation from the next 2 daily forecast entries (48h)."""
    total = 0.0
    # forecasts[0] is today; forecasts[1] and [2] are next 2 days
    for fc in forecasts[1:3]:
        p = fc.get("precipitation")
        if p is not None:
            total += float(p)
    return round(total, 3)


# ---------------------------------------------------------------------------
# Runtime advice string
# ---------------------------------------------------------------------------
def _runtime_str(deficit, rate):
    """
    Generate run-time recommendation string.
    Always targets 1.0\" application for clay.
    """
    target = TARGET_APPLICATION
    if rate <= 0:
        rate = DEFAULT_RATE
    total_min = round(target / rate * 60.0)
    half_min = round(total_min / 2)
    return f"approx {total_min} min (2 cycles of {half_min} min)"


# ---------------------------------------------------------------------------
# Publish output sensors
# ---------------------------------------------------------------------------
def _publish_sensors(lawn_advice, lawn_deficit, planter_advice, et0,
                     runtime_str, forecast_rain_48h):
    """Write all output sensors via state.set."""
    state.set(SENS_LAWN_ADVICE, lawn_advice, {
        "friendly_name": "Irrigation Lawn Advice",
        "icon": "mdi:sprinkler",
    })
    state.set(SENS_LAWN_DEFICIT, round(lawn_deficit, 3), {
        "friendly_name": "Irrigation Lawn Deficit",
        "unit_of_measurement": "in",
        "icon": "mdi:water-minus",
    })
    state.set(SENS_PLANTER_ADVICE, planter_advice, {
        "friendly_name": "Irrigation Planter Advice",
        "icon": "mdi:flower",
    })
    state.set(SENS_ET0_TODAY, round(et0, 4), {
        "friendly_name": "Irrigation ET0 Today",
        "unit_of_measurement": "in/day",
        "icon": "mdi:sun-thermometer",
    })
    state.set(SENS_LAWN_RUNTIME, runtime_str, {
        "friendly_name": "Irrigation Lawn Run Time",
        "icon": "mdi:timer-outline",
    })
    state.set(SENS_FCST_RAIN_48H, round(forecast_rain_48h, 3), {
        "friendly_name": "Irrigation Forecast Rain 48h",
        "unit_of_measurement": "in",
        "icon": "mdi:weather-rainy",
    })


# ---------------------------------------------------------------------------
# Core compute logic
# ---------------------------------------------------------------------------
def _run_daily_compute(update_deficit=True):
    """
    Run ET model and update state + sensors.

    update_deficit : True on nightly run (deficit accumulates);
                     False on startup/refresh (just republish sensors with
                     current forecast but keep stored deficit unchanged).
    """
    # --- Temperature from forecast ---
    forecasts = _get_daily_forecasts()
    tmax, tmin = _get_today_temps_from_forecast(forecasts)
    forecast_rain_48h = _get_forecast_precip_48h(forecasts)

    # Fall back to 70/55 if forecast unavailable
    if tmax is None:
        tmax = 70.0
        log.warning("irrigation: no forecast Tmax, using 70°F fallback")
    if tmin is None:
        tmin = 55.0
        log.warning("irrigation: no forecast Tmin, using 55°F fallback")
    tmax = float(tmax)
    tmin = float(tmin)

    # --- Humidity ---
    try:
        rh_mean = float(state.get(HUMIDITY_ENTITY) or 60)
    except Exception:
        rh_mean = 60.0

    # --- Day of year ---
    today = dt_mod.date.today()
    doy = today.timetuple().tm_yday

    # --- ET0 ---
    et0 = _compute_et0(tmax, tmin, rh_mean, doy)

    # --- Rain delta (from monotonic rain_total counter) ---
    try:
        current_rain_total = float(state.get(RAIN_TOTAL_ENTITY) or 0)
    except Exception:
        current_rain_total = 0.0

    if update_deficit:
        # Daily rain = delta from last run
        last_rt = _st["last_rain_total"]
        if last_rt is None:
            # First run: bootstrap with no rain credit (conservative)
            daily_rain = 0.0
            log.info("irrigation: first run, bootstrapping rain_total baseline")
        else:
            daily_rain = max(0.0, current_rain_total - float(last_rt))

        # Update last_rain_total
        _st["last_rain_total"] = current_rain_total

        # Lawn ET and effective rain
        lawn_et = et0 * KC_LAWN
        eff_rain = _effective_rain(daily_rain)

        # Update deficit
        new_deficit = _st["lawn_deficit"] + lawn_et - eff_rain
        new_deficit = max(0.0, min(DEFICIT_MAX, new_deficit))
        _st["lawn_deficit"] = new_deficit

        # Planter heat stress counter (no rain credit for planters)
        tmax_f = float(tmax)
        if tmax_f >= 85.0:
            _st["planter_heat_days"] += 1.0
        elif tmax_f >= 75.0:
            _st["planter_heat_days"] += 0.5
        # else no increment

        log.info(
            f"irrigation: daily compute — ET0={et0:.4f}\" lawn_ET={lawn_et:.4f}\" "
            f"rain={daily_rain:.3f}\" eff={eff_rain:.3f}\" "
            f"deficit={new_deficit:.3f}\" heat_days={_st['planter_heat_days']:.1f}"
        )
        _save_state()
    else:
        # Startup/refresh: ensure rain_total baseline is set if not already
        if _st["last_rain_total"] is None:
            _st["last_rain_total"] = current_rain_total
            _save_state()
            log.info(f"irrigation: bootstrapped rain_total baseline: {current_rain_total}")

    # --- Rate ---
    try:
        rate = float(state.get(RATE_ENTITY) or DEFAULT_RATE)
        if rate <= 0:
            rate = DEFAULT_RATE
    except Exception:
        rate = DEFAULT_RATE

    # --- Lawn advice ---
    deficit = _st["lawn_deficit"]
    if forecast_rain_48h >= FORECAST_SKIP_THRESHOLD:
        lawn_advice = f"Skip - {forecast_rain_48h:.2f}\" rain expected"
    elif deficit >= DEFICIT_TRIGGER:
        lawn_advice = f"Water lawn - apply ~1.0\""
    else:
        lawn_advice = f"Lawn OK - deficit {deficit:.2f}\""

    # --- Planter advice ---
    hd = _st["planter_heat_days"]
    if hd >= 2.0:
        planter_advice = "Water planters now - heat stress"
    elif hd >= 1.0:
        planter_advice = "Check planters - likely need water"
    else:
        planter_advice = "Planters OK"

    # --- Runtime string ---
    rt_str = _runtime_str(deficit, rate)

    # --- Publish sensors ---
    _publish_sensors(lawn_advice, deficit, planter_advice, et0, rt_str, forecast_rain_48h)

    log.info(
        f"irrigation: lawn='{lawn_advice}' deficit={deficit:.3f}\" "
        f"planters='{planter_advice}' ET0={et0:.4f}\" forecast48h={forecast_rain_48h:.3f}\""
    )


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------

@time_trigger("startup")
def on_startup():
    """Load persisted state and publish sensors on startup."""
    log.info("irrigation: startup — loading state")
    _load_state()
    _run_daily_compute(update_deficit=False)
    log.info("irrigation: startup complete")


@time_trigger("cron(55 23 * * *)")
def nightly_compute():
    """End-of-day model update at 23:55 — full day's rain captured."""
    log.info("irrigation: nightly compute triggered")
    _run_daily_compute(update_deficit=True)


@time_trigger("cron(0 7 * * *)")
def morning_notification():
    """
    Morning irrigation notification at 07:00.
    Sends to notify.itelephone if lawn or planters need attention.
    Notify service discovered: itelephone (main phone).
    """
    lawn_adv   = state.get(SENS_LAWN_ADVICE)   or ""
    plant_adv  = state.get(SENS_PLANTER_ADVICE) or ""
    lawn_needs = "Water lawn" in lawn_adv
    plant_needs = ("Water planters" in plant_adv or "Check planters" in plant_adv)
    if not (lawn_needs or plant_needs):
        return

    parts = []
    if lawn_needs:
        rt = state.get(SENS_LAWN_RUNTIME) or ""
        deficit = state.get(SENS_LAWN_DEFICIT) or "?"
        parts.append(f"Water lawn (~1.0\", {rt}, deficit {deficit}\")")
    if "Water planters now" in plant_adv:
        parts.append("Water planters now (heat stress)")
    elif "Check planters" in plant_adv:
        parts.append("Check planters (likely need water)")

    message = "Irrigation: " + ". ".join(parts)
    try:
        notify.itelephone(message=message, title="Irrigation Advisor")
        log.info(f"irrigation: morning notification sent: {message}")
    except Exception as e:
        log.warning(f"irrigation: failed to send notification: {e}")


@state_trigger("input_boolean.irrigation_lawn_watered")
def on_lawn_watered(value=None, old_value=None):
    """User tapped 'Watered lawn today' — reset deficit and turn boolean back off."""
    if value != "on":
        return
    _st["lawn_deficit"] = 0.0
    _st["lawn_last_watered"] = dt_mod.date.today().isoformat()
    _save_state()
    # Turn boolean back off (momentary button)
    try:
        input_boolean.turn_off(entity_id=LAWN_WATERED_BOOL)
    except Exception as e:
        log.warning(f"irrigation: failed to turn off lawn_watered: {e}")
    # Refresh sensors
    _run_daily_compute(update_deficit=False)
    log.info("irrigation: lawn watered — deficit reset to 0")


@state_trigger("input_boolean.irrigation_planters_watered")
def on_planters_watered(value=None, old_value=None):
    """User tapped 'Watered planters today' — reset heat_days and turn boolean back off."""
    if value != "on":
        return
    _st["planter_heat_days"] = 0.0
    _st["planter_last_watered"] = dt_mod.date.today().isoformat()
    _save_state()
    # Turn boolean back off (momentary button)
    try:
        input_boolean.turn_off(entity_id=PLANTER_WATERED_BOOL)
    except Exception as e:
        log.warning(f"irrigation: failed to turn off planters_watered: {e}")
    # Refresh sensors
    _run_daily_compute(update_deficit=False)
    log.info("irrigation: planters watered — heat_days reset to 0")


@state_trigger("input_number.irrigation_sprinkler_rate")
def on_rate_changed(value=None, old_value=None):
    """Sprinkler rate changed — recompute runtime string."""
    _run_daily_compute(update_deficit=False)
