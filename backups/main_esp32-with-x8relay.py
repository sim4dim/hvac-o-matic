from machine import Pin, SoftI2C
import network
import utime
import ujson
import usocket as socket
from ssd1306 import SSD1306_I2C
from bme280 import BME280
from bme680 import BME680_I2C

# Wi-Fi credentials
WIFI_SSID = 'easy'
WIFI_PASSWORD = 'YOUR_WIFI_PASSWORD'

# OLED setup
i2c = SoftI2C(scl=Pin(22), sda=Pin(21))
oled = SSD1306_I2C(128, 64, i2c)

# State variables
bypass_state = False
offline_mode = False
screen_state = 0
last_screen_switch = utime.ticks_ms()
delta_pressure_threshold = 2.0  # hPa

# Relay pins for ESP32 WROOM board
relay_pins = [12, 13, 14, 26, 33, 32, 25, 27]

modes = {
    "HEAT": {"pin": Pin(13, Pin.OUT), "state": 0},
    "COOL": {"pin": Pin(12, Pin.OUT), "state": 0},
    "FAN": {"pin": Pin(14, Pin.OUT), "state": 0},
}
uv_relay = Pin(26, Pin.OUT)
bypass_pin = Pin(27, Pin.OUT)
warning_led = Pin(23, Pin.OUT)
bypass_button = Pin(0, Pin.IN, Pin.PULL_UP)

# Debounce
last_button_state = False
debounce_time = 0

# Sensor placeholders
supply_data = {"temperature": 0, "humidity": 0, "pressure": 0}
return_data = {"temperature": 0, "humidity": 0, "pressure": 0, "voc": 0}

# Sensor initialization
bme_supply = None
bme_return = None
devices = i2c.scan()

if 0x76 in devices:
    try:
        bme_supply = BME280(i2c=i2c, address=0x76)
        print("BME280 detected.")
    except Exception as e:
        print(f"Error initializing BME280: {e}")
else:
    print("Warning: BME280 not detected.")

if 0x77 in devices:
    try:
        bme_return = BME680(i2c=i2c, address=0x77)
        bme_return.set_humidity_oversample(BME680.OS_2X)
        bme_return.set_pressure_oversample(BME680.OS_4X)
        bme_return.set_temperature_oversample(BME680.OS_8X)
        bme_return.set_filter(BME680.FILTER_SIZE_3)
        bme_return.set_gas_status(BME680.ENABLE_GAS_MEAS)
        bme_return.set_gas_heater_temperature(320)
        bme_return.set_gas_heater_duration(150)
        bme_return.select_gas_heater_profile(0)
        print("BME680 detected.")
    except Exception as e:
        print(f"Error initializing BME680: {e}")
else:
    print("Warning: BME680 not detected.")

# Update OLED
def update_oled():
    global screen_state
    oled.fill(0)
    if screen_state == 0:
        oled.text(f"Bypass: {'ON' if bypass_state else 'OFF'}", 0, 0)
        oled.text(f"HEAT: {'ON' if modes['HEAT']['state'] else 'OFF'}", 0, 10)
        oled.text(f"COOL: {'ON' if modes['COOL']['state'] else 'OFF'}", 0, 20)
        oled.text(f"FAN: {'ON' if modes['FAN']['state'] else 'OFF'}", 0, 30)
        ip = network.WLAN(network.STA_IF).ifconfig()[0] if not offline_mode else "Offline"
        oled.text(f"IP: {ip}", 0, 50)
    else:
        oled.text("SENSOR DATA:", 0, 0)
        oled.text(f"Sup T: {supply_data['temperature']:.1f}C", 0, 10)
        oled.text(f"Ret T: {return_data['temperature']:.1f}C", 0, 20)
        delta_p = supply_data['pressure'] - return_data['pressure']
        oled.text(f"Delta P: {delta_p:.2f}hPa", 0, 30)
        oled.text(f"VOC: {return_data['voc']:.2f} kOhm", 0, 40)
    oled.show()

# Switch OLED screen
def switch_screen():
    global screen_state, last_screen_switch
    if utime.ticks_diff(utime.ticks_ms(), last_screen_switch) > 5000:  # 5 seconds interval
        screen_state = 1 - screen_state
        last_screen_switch = utime.ticks_ms()
        update_oled()

# Read sensors
def read_sensors():
    global supply_data, return_data
    try:
        if bme_supply:
            supply_data = {
                "temperature": float(bme_supply.temperature),
                "humidity": float(bme_supply.humidity),
                "pressure": float(bme_supply.pressure),
            }
        if bme_return and bme_return.get_sensor_data():
            return_data = {
                "temperature": float(bme_return.data.temperature),
                "humidity": float(bme_return.data.humidity),
                "pressure": float(bme_return.data.pressure),
                "voc": float(bme_return.data.gas_resistance / 1000),  # Convert to kOhm
            }
    except Exception as e:
        print(f"Sensor error: {e}")


def initialize_relays():
    """Initialize all relay pins and turn them off."""
    for pin in relay_pins:
        relay = Pin(pin, Pin.OUT)
        relay.value(0)  # Assuming LOW (0) is the "off" state for the relay
    print("All relays initialized and turned off.")



# Toggle relay modes
def toggle_mode(mode, action):
    if bypass_state:
        print("Bypass mode active. No changes allowed.")
        return
    if mode not in modes:
        return
    if action == "on":
        if mode == "HEAT":
            modes["COOL"]["pin"].value(0)
            modes["COOL"]["state"] = 0
            utime.sleep(0.1)  # 100ms delay to ensure safe switch
        elif mode == "COOL":
            modes["HEAT"]["pin"].value(0)
            modes["HEAT"]["state"] = 0
            utime.sleep(0.1)  # 100ms delay to ensure safe switch
        elif mode == "FAN":
            modes["HEAT"]["pin"].value(0)
            modes["HEAT"]["state"] = 0
            modes["COOL"]["pin"].value(0)
            modes["COOL"]["state"] = 0
        modes[mode]["pin"].value(1)
        modes[mode]["state"] = 1
        uv_relay.value(1)
        if mode in ["HEAT", "COOL"]:
            modes["FAN"]["pin"].value(1)
            modes["FAN"]["state"] = 1
            
    elif action == "off":
        modes[mode]["pin"].value(0)
        modes[mode]["state"] = 0
        uv_relay.value(0)
        if modes["HEAT"]["state"] == 0 and modes["COOL"]["state"] == 0 and mode != "FAN":
            modes["FAN"]["pin"].value(0)
            modes["FAN"]["state"] = 0

# Toggle bypass state
def toggle_bypass():
    global bypass_state
    bypass_state = not bypass_state
    bypass_pin.value(1 if bypass_state else 0)
    warning_led.value(1 if bypass_state else 0)
    for mode in modes.values():
        mode["pin"].value(0)
        mode["state"] = 0
    uv_relay.value(0)
    update_oled()
    print(f"Bypass {'enabled' if bypass_state else 'disabled'}.")

# Check for button press with debounce
def check_bypass_button():
    global last_button_state, debounce_time
    current_state = not bypass_button.value()
    now = utime.ticks_ms()
    if current_state != last_button_state and (now - debounce_time) > 200:
        debounce_time = now
        if current_state:
            toggle_bypass()
    last_button_state = current_state

# HTTP request handling
def handle_request(client):
    try:
        request = client.recv(1024).decode("utf-8")
        request_line = request.split("\r\n")[0]
        method, path, *_ = request_line.split()
        if method == "GET" and path == "/":
            content = (
                "<!DOCTYPE html>"
                "<html><head><title>HVAC Status</title></head><body>"
                "<h1>Current State</h1>"
                "<p>Bypass: " + ("ON" if bypass_state else "OFF") + "</p>"
                "<ul>"
            )
            for mode, data in modes.items():
                content += "<li>" + mode + ": " + ("ON" if data["state"] else "OFF") + "</li>"
            content += "</ul></body></html>"
            client.send("HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n" + content)
        elif method == "GET" and path == "/state":
            state = {
                "bypass": "ON" if bypass_state else "OFF",
                "modes": {mode: ("ON" if data["state"] else "OFF") for mode, data in modes.items()},
                "supply": supply_data,
                "return": return_data,
                "pressure_diff": supply_data["pressure"] - return_data["pressure"],
                "humidity_diff": supply_data["humidity"] - return_data["humidity"],
                "timestamp": utime.time(),
            }
            client.send("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n" + ujson.dumps(state))
        elif method == "POST" and path == "/changeMode":
            content_length = int(next(line for line in request.split("\r\n") if "Content-Length" in line).split(":")[1])
            body = ujson.loads(request.split("\r\n\r\n", 1)[1][:content_length])
            mode = body.get("mode", "").upper()
            action = body.get("action", "").lower()
            toggle_mode(mode, action)
            response = {
                "status": "success",
                "mode": mode,
                "action": action,
                "timestamp": utime.time(),
            }
            client.send("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n" + ujson.dumps(response))
        else:
            client.send("HTTP/1.1 404 Not Found\r\nContent-Type: text/plain\r\n\r\n404 Not Found")
    except Exception as e:
        print("Error handling request:", e)
        client.send("HTTP/1.1 500 Internal Server Error\r\nContent-Type: text/plain\r\n\r\nInternal Server Error")
    finally:
        client.close()
# Start web server
def start_web_server():
    addr = socket.getaddrinfo("0.0.0.0", 80)[0][-1]
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(addr)
    s.listen(5)
    print("Web server running on http://0.0.0.0:80/")
    while True:
        client, _ = s.accept()
        handle_request(client)

# Main loop
def main():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)
    for _ in range(10):
        if wlan.isconnected():
            break
        utime.sleep(1)

    initialize_relays()
    print("Starting HVAC ESP32 Controller...")
    update_oled()
    while True:
        read_sensors()
        switch_screen()
        check_bypass_button()
        utime.sleep(0.1)

import _thread
_thread.start_new_thread(start_web_server, ())
main()
