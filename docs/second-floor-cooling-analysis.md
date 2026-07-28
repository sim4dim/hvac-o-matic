# Second-Floor Cooling Deficit — Diagnosis & Fix Options

**Date:** 2026-07-02  **Status:** PARKED (analysis only — no action taken)

## TL;DR
The upstairs bedrooms (Ben, Master Bedroom) can't hold setpoint on hot afternoons; the
first floor never even calls for cooling. This is **not** a broken, undersized, or
airflow-starved AC. It's **daytime heat gain** — attic/roof radiant heat + west/south
window solar — overwhelming the rooms during peak sun. The cheap fixes (shades/film +
attic work) address the root cause; a mini-split would be brute-forcing a reducible gain.

## The symptom
- MBR & Ben are the two cooling "hogs" (~29 h and ~26 h over one week).
- Ben cools continuously for hours and **stalls ~77.4°F**, never reaching its stop threshold on hot days.
- First Floor: **0 cooling hours** in a week (ground floor stays naturally cool).

## UPDATE 2026-07-02 (empirical): airflow imbalance was ALSO a factor
After this analysis, the owner **manually closed an always-open upstairs bathroom register**
and Ben's room began cooling & satisfying in reasonable time. That register (a low-priority
room nobody needs cooled) was bleeding cold-air **volume** off the shared trunk; closing it
redirected CFM to Ben. This confirms the one variable the data couldn't measure (CFM): Ben
was **under-supplied on volume due to system imbalance**, not just heat-gain-limited.
Revised picture = **both**: heat gain (attic+windows) sets the difficulty AND airflow
imbalance was starving Ben of volume. Cheapest fix is **manual air balancing** — throttle
low-priority/always-open registers (over-served first floor, bathrooms, hallways) to push
air to Ben/MBR. Safety: don't over-close (watch `sensor.hvac_controller_supply_temperature`
for coil-freeze < ~40°F; cutout at 38°F). Static is a non-issue (dP ~1.3 vs 10 hPa limit).

## What we checked (and the nuance)
- **Delivery temperature fine; VOLUME was improvable.** Register air stays cold (53–55°F) the
  entire call and vents sit at 100% open, so cold-air *delivery* is not the problem. But total
  *volume* to Ben depended on system balance — see the 2026-07-02 update above: an open
  bathroom register was diverting flow, and closing it materially helped.
- **Undersized AC (total capacity) — ruled out.** First floor is easily satisfied and Gene
  (same 79°F setpoint) satisfies and doesn't even run during Ben's afternoon marathons. The
  equipment has the tons; the problem is localized upstairs.
- **Pressure sensors can't help.** The ESP32 `pressure_delta` is a single supply−return
  differential (total static, ~1–2 hPa, never near the 10 hPa cutout) — can't separate
  supply vs. return and is structurally blind to a single branch. Keen per-vent "pressure"
  is absolute barometric at ~75 Pa resolution — no usable starvation signal either.

## The evidence (verified from 48 h of HA history)
**Same hardware, radically different by time of day (Ben's room cooling rate):**

| When | Outdoor | Cooling rate |
|------|---------|--------------|
| Overnight 9pm–9am | 82–86°F | **−1.7 to −2.2 °F/hr** ✅ |
| Midday marathon 11:32am–6:15pm | ~94°F | **−0.28 °F/hr** ❌ |

A duct/airflow restriction cannot switch on and off with the sun. Cooling that is ~6× worse
in daylight = solar/roof heat gain, not a physical restriction. MBR shows the same pattern
(night −1.8 to −2.2 °F/hr; midday −0.31 °F/hr).

**Representative afternoon cycle (2026-07-01, Ben):** room 77.9°F → 76.1°F over 6h20m of
continuous cooling, with register air 53–55°F and vent 100% the whole time.

| Local | Room °F | Register °F | Vent % | Outdoor °F |
|-------|---------|-------------|--------|------------|
| 11:46 | 77.9 | 55.1 | 100 | 91.6 |
| 12:42 | 77.5 | 54.1 | 100 | 93.5 |
| 14:03 | 77.9 | 54.9 | 100 | 94.4 |
| 14:51 | 77.5 | 54.3 | 100 | 95.0 |
| 16:02 | 76.8 | 53.5 | 100 | 93.6 |
| 17:36 | 76.5 | 53.8 | 100 | 93.0 |
| 18:01 | 76.1 | 54.6 | 100 | 93.7 |

## Root cause (confirmed with owner)
- **Attic/roof directly above** these bedrooms → afternoon roof radiant heat down through the ceiling.
- **Large west/south windows with strong afternoon sun** → direct solar gain.
- Both peak in the afternoon, matching the "fine at night, hopeless midday" signature exactly.

## Recommended fixes — cheap → structural (NOT mini-split first)
1. **Windows (now, cheap, DIY):** solar/low-e window film + blackout or cellular (honeycomb)
   shades on the sun-facing bedroom windows; close them before the afternoon sun.
2. **Attic (biggest structural driver; helps winter too):** radiant barrier on the rafters +
   top up insulation to R-49/R-60 + improve attic ventilation (ridge/soffit vents or solar
   attic fan). Attics hit 130–150°F on days like this.
3. **Mini-split — only if 1 & 2 aren't enough.** It works, but it's paying to fight a gain you
   can cheaply reduce; reassess after plugging the heat gain.
4. **Bill (separate from comfort):** raising the hog-zone setpoints and letting them drift
   during the on-peak TOU window cuts cost — but won't fix midday *comfort*; only cutting the
   gain does that. (Rate: Summer On-Peak $0.271, Off-Peak $0.219 /kWh.)

## Cheap diagnostic to confirm/monitor
Add an **Ecowitt WH31 temp sensor in the attic** (~$15, fits the existing GW1000). Directly
confirms the attic is the driver and gives a before/after signal when a radiant barrier is added.

## Data sources (for reproducing)
- Room temps: `sensor.gw1000_temp_ch7` (Ben), `_ch4` (MBR), `_ch6` (Gene), `sensor.gw1000_indoor_temperature` (1st floor)
- Register temps: `sensor.keen_ben_temperature`, `sensor.keen_mbr_1/2_temperature`, `sensor.keen_gene_temperature`
- Vent %: `sensor.keenect_vent_{zone}`; Supply: `sensor.hvac_controller_supply_temperature`; Outdoor: `sensor.weewx_outdoor_temperature`
- Method: HA `history/history_during_period`, 48 h window ending 2026-07-02.
