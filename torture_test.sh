#!/usr/bin/env bash
# ESP32-S3 ESPHome Controller Torture Test
# Target: 192.168.1.90, 100 cycles

set -u

HOST="192.168.1.90"
BASE="http://${HOST}"
MAX_TIME=5
CYCLES=100
FAIL_COUNT=0
TOTAL_REQUESTS=0
CYCLE_COMPLETED=0

# Switches
SWITCHES=("Heat" "Cool" "Fan" "UV" "Bypass")

# Sensors
declare -A SENSORS=(
  ["Uptime"]="/sensor/Uptime"
  ["ESP32_Temp"]="/sensor/ESP32%20Temperature"
  ["WiFi_Signal"]="/sensor/WiFi%20Signal"
)

# --- helpers ---

do_post() {
  local url="$1"
  TOTAL_REQUESTS=$((TOTAL_REQUESTS + 1))
  local http_code
  http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time "$MAX_TIME" \
    -X POST -H "Content-Length: 0" "${BASE}${url}" 2>/dev/null)
  if [[ "$http_code" != "200" ]]; then
    FAIL_COUNT=$((FAIL_COUNT + 1))
    return 1
  fi
  return 0
}

get_sensor() {
  local url="$1"
  TOTAL_REQUESTS=$((TOTAL_REQUESTS + 1))
  local body
  body=$(curl -s --max-time "$MAX_TIME" "${BASE}${url}" 2>/dev/null)
  if [[ $? -ne 0 || -z "$body" ]]; then
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo "ERR"
    return 1
  fi
  # ESPHome REST API returns JSON with "value" field
  local val
  val=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin).get('value','N/A'))" 2>/dev/null)
  if [[ -z "$val" ]]; then
    echo "$body"  # might be plain text
  else
    echo "$val"
  fi
}

get_switch_state() {
  local sw="$1"
  TOTAL_REQUESTS=$((TOTAL_REQUESTS + 1))
  local body
  body=$(curl -s --max-time "$MAX_TIME" "${BASE}/switch/${sw}" 2>/dev/null)
  if [[ $? -ne 0 || -z "$body" ]]; then
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo "ERR"
    return 1
  fi
  local val
  val=$(echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('state','N/A'))" 2>/dev/null)
  if [[ -z "$val" ]]; then
    echo "$body"
  else
    echo "$val"
  fi
}

check_alive() {
  TOTAL_REQUESTS=$((TOTAL_REQUESTS + 1))
  local http_code
  http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time "$MAX_TIME" "${BASE}/sensor/Uptime" 2>/dev/null)
  if [[ "$http_code" != "200" ]]; then
    FAIL_COUNT=$((FAIL_COUNT + 1))
    return 1
  fi
  return 0
}

print_status() {
  local cycle=$1
  local start_ms=$2

  local uptime esp_temp wifi_sig resp_time
  uptime=$(get_sensor "/sensor/Uptime")
  esp_temp=$(get_sensor "/sensor/ESP32%20Temperature")
  wifi_sig=$(get_sensor "/sensor/WiFi%20Signal")

  local end_ms
  end_ms=$(date +%s%3N)
  resp_time=$(( end_ms - start_ms ))

  printf "[Cycle %3d] uptime=%-8s  esp_temp=%-8s  wifi=%-8s  resp=%dms  fails=%d/%d\n" \
    "$cycle" "$uptime" "$esp_temp" "$wifi_sig" "$resp_time" "$FAIL_COUNT" "$TOTAL_REQUESTS"
}

# --- main ---

echo "=============================================="
echo " ESP32-S3 Torture Test — ${HOST}"
echo " Cycles: ${CYCLES}"
echo " Started: $(date)"
echo "=============================================="
echo ""

# Pre-flight check
echo -n "Pre-flight connectivity check... "
if check_alive; then
  echo "OK"
else
  echo "FAILED — device not reachable at ${BASE}"
  exit 1
fi

# Initial sensor snapshot
echo ""
echo "--- Initial Sensor Readings ---"
for name in "${!SENSORS[@]}"; do
  val=$(get_sensor "${SENSORS[$name]}")
  printf "  %-15s : %s\n" "$name" "$val"
done
echo ""

# Ensure all switches off before starting
echo -n "Resetting all switches to OFF... "
for sw in "${SWITCHES[@]}"; do
  do_post "/switch/${sw}/turn_off"
done
sleep 1
echo "done"
echo ""

# === Main loop ===
for (( cycle=1; cycle<=CYCLES; cycle++ )); do

  # --- Phase 1: Rapid toggle all switches on then off ---
  for sw in "${SWITCHES[@]}"; do
    do_post "/switch/${sw}/turn_on"
    sleep 0.5
  done
  for sw in "${SWITCHES[@]}"; do
    do_post "/switch/${sw}/turn_off"
    sleep 0.5
  done

  # Alive check after phase 1
  if ! check_alive; then
    echo "*** DEVICE UNRESPONSIVE after phase 1 (toggle) at cycle $cycle ***"
  fi

  # --- Phase 2: Interlock test (Heat + Cool simultaneously) ---
  do_post "/switch/Heat/turn_on"
  sleep 0.2
  do_post "/switch/Cool/turn_on"
  sleep 1

  heat_state=$(get_switch_state "Heat")
  cool_state=$(get_switch_state "Cool")

  if [[ "$heat_state" == "ON" && "$cool_state" == "ON" ]]; then
    echo "[Cycle $cycle] *** INTERLOCK FAILURE: Heat=$heat_state Cool=$cool_state — both ON! ***"
  fi

  # Clean up
  do_post "/switch/Heat/turn_off"
  do_post "/switch/Cool/turn_off"
  sleep 0.3

  # Alive check after phase 2
  if ! check_alive; then
    echo "*** DEVICE UNRESPONSIVE after phase 2 (interlock) at cycle $cycle ***"
  fi

  # --- Phase 3: Rapid-fire Bypass toggle (10x at 0.1s) ---
  for (( i=0; i<10; i++ )); do
    do_post "/switch/Bypass/turn_on"
    sleep 0.1
    do_post "/switch/Bypass/turn_off"
    sleep 0.1
  done

  # Ensure bypass off
  do_post "/switch/Bypass/turn_off"

  # Alive check after phase 3
  if ! check_alive; then
    echo "*** DEVICE UNRESPONSIVE after phase 3 (rapid-fire) at cycle $cycle ***"
  fi

  CYCLE_COMPLETED=$cycle

  # --- Status report every 10 cycles ---
  if (( cycle % 10 == 0 )); then
    ts=$(date +%s%3N)
    print_status "$cycle" "$ts"
  fi

done

# === Final Report ===
echo ""
echo "=============================================="
echo " TORTURE TEST COMPLETE"
echo "=============================================="
echo " Finished: $(date)"
echo " Cycles completed: ${CYCLE_COMPLETED} / ${CYCLES}"
echo " Total HTTP requests: ${TOTAL_REQUESTS}"
echo " Failed requests: ${FAIL_COUNT}"
echo " Failure rate: $(echo "scale=2; ${FAIL_COUNT}*100/${TOTAL_REQUESTS}" | bc)%"
echo ""
echo "--- Final Sensor Readings ---"
for name in "${!SENSORS[@]}"; do
  val=$(get_sensor "${SENSORS[$name]}")
  printf "  %-15s : %s\n" "$name" "$val"
done
echo ""

# Final switch states
echo "--- Final Switch States ---"
for sw in "${SWITCHES[@]}"; do
  state=$(get_switch_state "$sw")
  printf "  %-10s : %s\n" "$sw" "$state"
done
echo ""

if [[ $FAIL_COUNT -eq 0 ]]; then
  echo "RESULT: PASS — zero failures across $TOTAL_REQUESTS requests"
elif [[ $FAIL_COUNT -lt 5 ]]; then
  echo "RESULT: MARGINAL — $FAIL_COUNT failures out of $TOTAL_REQUESTS requests"
else
  echo "RESULT: FAIL — $FAIL_COUNT failures out of $TOTAL_REQUESTS requests"
fi
echo "=============================================="
