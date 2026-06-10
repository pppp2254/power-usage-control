# Runs once at boot. Connect WiFi then hand off to main.py.
import network
import time
import config

def connect_wifi():
    # Retry forever: proceeding without WiFi just crashes main.py later.
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    attempt = 1
    while not wlan.isconnected():
        print("[boot] wifi connect attempt", attempt, ":", config.WIFI_SSID)
        wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
        t0 = time.ticks_ms()
        while not wlan.isconnected() and time.ticks_diff(time.ticks_ms(), t0) < 30000:
            time.sleep_ms(200)
        attempt += 1
    print("[boot] wifi:", wlan.ifconfig())

connect_wifi()
