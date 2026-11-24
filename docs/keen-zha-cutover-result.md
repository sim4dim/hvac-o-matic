# Keen Ben + Gene: keenect cut over to ZHA covers (2026-11-24)

From: hvac session. For: housekeeping (reply to keen-gene-zha-handoff.md).
Status as of 2026-11-24 12:49 EST. Checked live via the HA WebSocket API, ZHA attribute reads, HA logbook/history and the HA container log.

## Result
keenect v2.6.16 drives `cover.keen_ben_zha` and `cover.keen_gene_zha`. MBR 1/2 stay on Hubitat (`light.keen_mbr_*`), unchanged.

- Zone helpers: zone 1 (ben) and zone 2 (gene) have `vent_1 = cover.keen_<x>_zha` and `vent_type = cover`. Both are persisted.
  - `keenect_zones.yaml` now has `initial: cover` for both. vent_type is not persisted by keenect, so `initial` decides it after every HA restart.
- No Hubitat eventsJson poll and no `clearObstruction` for Ben/Gene. `_KEEN_HUBITAT_ID` holds only mbr_1/mbr_2.
- `sensor.keenect_vent_real_ben/gene` keep their names and are now built from HA data:
  - ZHA availability;
  - `last_reported` of `sensor.keen_<x>_zha_temperature/_pressure` (offline after 60 min of silence);
  - `current_position`.
- `ha/packages/keen_vent_alerts.yaml`: the Ben/Gene offline alerts link to the HA device page and have no Retry button. Retry is accepted only for mbr_1/mbr_2.
- Dashboard: the Ben/Gene vent rows now use the ZHA covers.
- The leftover Hubitat "Vent Offline" notifications for Ben/Gene were dismissed automatically. `sensor.keen_vents_offline` = 0.

## Verified (no forced furnace run, per the user)
- Direct commands 0/15/50/100 on both vents. The device LevelControl/OnOff read back matched every time (50% gave 128 on Ben and 134 on Gene).
- 12:28:47 and 12:42:42: keenect itself closed Ben (logbook context: pyscript) while IDLE.
- A genuine heat call started 12:44:24 (Ben's room 62.8 °F vs a 63 °F setpoint). keenect opened Ben to 100 at 12:44:27 and kept Gene at 0.
- Closed loop: Ben moved by hand to 50 at 12:46:02. keenect logged "target=100 position=50" and restored 100 at 12:46:18.

## Quirks found (all handled in keenect)
1. **Cover `state` lags.** It was wrong for 13-15 s+ (Gene showed "open" at position 0 and "closed" at 50). keenect uses only `current_position`.
2. **pyscript StateVal trap.** A vent id read from an input_select helper is a StateVal. `state.getattr(<StateVal>)` returns the helper's attributes (its options list), not the cover's, so `current_position` was always None. Fixed with `str()` when zones load and in `_cover_position`. The light code avoided this only by accident: it reads via f-string first.
3. **Slow, ordered calls.** Each `set_cover_position` takes 1-4 s (ZHA waits for the vent's ack). Back-to-back commands apply in order.
4. **Reported as battery.** The vents report power source "Battery or Unknown" (AC eliminator, battery firmware), so ZHA marks them unavailable only after 6 h. keenect's 60-min check is the real offline signal.
5. **Pressure dropout on Ben.** `sensor.keen_ben_zha_pressure` has been silent since 12:09 while its temperature keeps reporting. Same per-attribute dropout seen on Hubitat. The liveness check uses either sensor, so this is not an alert.
6. **Deploy race.** Pyscript auto-reloads on file write. An `input_select.reload` afterwards wiped the dynamic keenect dropdowns. Recovered with `pyscript.keenect_migrate_config` + Apply. Recorded in the hvac deploy runbook.

## Known gap (pre-existing, not fixed)
After a keenect restart in the middle of a cycle, a vent that keenect has not commanded since the restart is not re-checked until the cycle ends. At cycle end all vents close. Seen only because a test moved Gene by hand during the cycle; Gene was put back to 0.

## Still open (user decision)
- Delete Hubitat devices 487/488 and their old HA entities (`light.keen_ben/gene`, `cover.keen_ben/gene`, `sensor.keen_ben/gene_*`). keenect no longer uses them. Not done; waiting for the user's OK.
- MBR 1/2 migration.
