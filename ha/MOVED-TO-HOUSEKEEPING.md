# Files Moved to housekeeping (2026-07-28)

Per the HA-config consolidation policy ("keep all HA config in housekeeping, unless it is
something highly specialised like HVAC that has many components"), the files below were general
HA config that only happened to live in hvac's repo. They have been copied to `housekeeping` with
a provenance header, and their originals here have been left in place with a `⚠️ MOVED` /
`⚠️ SUPERSEDED` header — **do not deploy the copies in this repo.**

hvac retains only genuinely HVAC-specialised config: Keenect zone control (`ha/packages/keenect.yaml`,
`ha/packages/keenect_zones.yaml`, `ha/dashboards/keenect.yaml`, `ha/pyscript/keenect_ha.py`),
thermostats (`ha/packages/thermostats.yaml`), HVAC ESPHome nodes (`esphome/*` — untouched by this
move), HVAC-specific cost/runtime/watchdog sensors (`ha/packages/hvac_air_quality.yaml`,
`ha/packages/hvac_filter_monitor.yaml`, `ha/packages/hvac_periodic_circulation.yaml`,
`ha/packages/hvac_watchdog.yaml`), and ventilation control (`ha/packages/bathroom_fan.yaml`,
treated as HVAC).

| old path (hvac) | new path (housekeeping) | date | reason |
|---|---|---|---|
| `ha/packages/barometer_monitor.yaml` | `housekeeping/ha-packages/barometer_monitor.yaml` | 2026-07-28 | Barometer trend/monitoring sensors — weather domain, not HVAC. Was the sha256-verified live-matching winner over barometer project's own (materially different, non-matching) copy. |
| `ha/packages/boat.yaml` | `housekeeping/ha-packages/boat.yaml` | 2026-07-28 | Boat MQTT sensors — boat is not HVAC (the user's original complaint that triggered this consolidation). |
| `ha/dashboards/boat.yaml` | `housekeeping/ha/dashboards/boat.yaml` | 2026-07-28 | Boat Lovelace dashboard. Content was freshly re-synced from live earlier today by a concurrent agent (header self-documents live sha256 `49f68fcc...`) before this move; the housekeeping copy carries that content forward, verified byte-identical below its own re-sync header. |
| `ha/packages/irrigation.yaml` | `housekeeping/ha-packages/irrigation.yaml` | 2026-07-28 | Irrigation input_number/input_boolean helpers — irrigation is housekeeping's domain. |
| `ha/pyscript/irrigation_advisor.py` | `housekeeping/ha/pyscript/irrigation_advisor.py` | 2026-07-28 | ET soil-water-balance irrigation advisor pyscript — irrigation is housekeeping's domain. |
| `ha/packages/rain_accumulation.yaml` | `housekeeping/ha-packages/rain_accumulation.yaml` | 2026-07-28 | WeeWX-sourced weekly/monthly rain accumulation utility_meter. Only consumers (`irrigation_advisor.py`, the weather dashboard) also moved to housekeeping today. |
| `ha/dashboards/weather.yaml` | `housekeeping/ha/dashboards/weather.yaml` | 2026-07-28 | Weather/WeeWX Lovelace dashboard incl. rain card. housekeeping already owns `weewx_sensors.yaml` and the WeeWX Pi. Content was freshly re-synced from live earlier today by a concurrent agent; verified byte-identical to housekeeping's own `ha-backups/weather-dashboard-post-rain-fix-2026-07-27.yaml` (both hash `b35a5181...`) before this move. |
| `ha/packages/weewx_sensors.yaml` | `housekeeping/ha-packages/weewx_sensors.yaml` | 2026-07-28 | WeeWX MQTT sensors incl. the 2026-07-27 wind-direction hold-last fix. housekeeping's copy was **already** the sha256-verified live-matching winner (created independently, not part of this move) — hvac's stale copy was already stubbed `⚠️ SUPERSEDED` by a concurrent agent before this pass; left as-is. |
| `ha/packages/library_outlet.yaml` | `housekeeping/ha-packages/library_outlet.yaml` | 2026-07-28 | Library outlet sunrise/sunset automation — general lighting, not HVAC. |
| `ha/packages/living_room_outlet.yaml` | `housekeeping/ha-packages/living_room_outlet.yaml` | 2026-07-28 | Living room outlet sunset/TV automation (reference-only, self-marked "do not deploy as a package" — managed via HA UI) — general lighting, not HVAC. |

## Explicitly NOT moved (left in hvac)

- **`ha/packages/outdoor_temp_template.yaml`** — AMBIGUOUS, left in hvac. Its header claims it
  feeds pyscript's HVAC cool-lockout logic, but `keenect_ha.py`'s actual `OUTDOOR_TEMP_ENTITY`
  constant reads `sensor.weewx_outdoor_temperature`, not this file's
  `sensor.outdoor_temperature_met` — no other file in the repo references
  `outdoor_temperature_met`. Unclear whether this template sensor is still live-wired into HVAC
  control or an orphaned dead fallback. Needs a user decision, not a guess.
- **`esphome/*`** (including `hvac-1st-floor.yaml`, `hvac-controller.yaml`, `hvac-spare.yaml`,
  `secrets.yaml`, `secrets.yaml.example`) — HVAC ESPHome firmware, untouched by this move.
  `hvac-1st-floor.yaml` in particular is repo-ahead-of-live and unresolved; do not touch it.
- Keenect zone control, thermostats, `hvac_air_quality.yaml`, `hvac_filter_monitor.yaml`,
  `hvac_periodic_circulation.yaml`, `hvac_watchdog.yaml`, `bathroom_fan.yaml` (ventilation) — all
  genuinely HVAC-specialised, stay in hvac per the consolidation policy.
