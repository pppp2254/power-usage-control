# Entry point. boot.py has already brought WiFi up.
import time
import config
from mqtt_client import Mqtt
from control import Controller

try:
    import urequests
    _MICROPY = True
except ImportError:
    import requests as urequests  # for desktop testing only
    _MICROPY = False

# time.time() epoch differs across ports: Unix (1970) on CPython,
# 2000-01-01 on ESP32 MicroPython. Normalize to Unix epoch ms.
_EPOCH_OFFSET_MS = 946684800000 if time.gmtime(0)[0] == 2000 else 0


def epoch_ms():
    return int(time.time() * 1000) + _EPOCH_OFFSET_MS


def sync_ntp():
    # Without this, ESP32 timestamps are seconds-since-boot garbage.
    if not _MICROPY:
        return
    try:
        import ntptime
        ntptime.settime()
        print("[main] ntp synced, epoch_ms =", epoch_ms())
    except Exception as e:
        print("[main] ntp sync failed (telemetry ts will be wrong):", e)


def fetch_power():
    try:
        r = urequests.get(config.SOLAR_URL, timeout=3)
        try:
            data = r.json()
        finally:
            r.close()
        return float(data["input_w"]), float(data["usage_w"])
    except Exception as e:
        print("[main] solar fetch error:", e)
        return None


def main():
    sync_ntp()

    mqtt = Mqtt(
        client_id=config.MQTT_CLIENT_ID,
        host=config.MQTT_HOST,
        port=config.MQTT_PORT,
        user=config.MQTT_USERNAME,
        password=config.MQTT_PASSWORD,
        ssl=config.MQTT_TLS,
    )

    def publish_telemetry():
        rem = ctrl.override_remaining_ms()
        payload = {
            "ts": epoch_ms(),
            "input_w": round(ctrl.last_input_w, 1),
            "usage_w": round(ctrl.last_usage_w, 1),
            "available_w": round(ctrl.last_input_w - ctrl.last_usage_w, 1),
            "mode": ctrl.mode,
            "ac": {
                "desired": ctrl.ac.desired,
                "commanded": ctrl.ac.commanded,
                "observed": ctrl.ac.observed,
            },
            "fan": {
                "desired": ctrl.fan.desired,
                "commanded": ctrl.fan.commanded,
                "observed": ctrl.fan.observed,
            },
            "override_until": (epoch_ms() + rem) if rem is not None else None,
        }
        mqtt.publish(config.TOPIC_POWER_TELEMETRY, payload, qos=1, retain=True)

    ctrl = Controller(mqtt, publish_telemetry)

    def on_msg(topic, msg):
        ctrl.on_cmd_esp32(topic, msg)

    mqtt.connect(on_message=on_msg)
    # Subscribe ONLY to cmd/esp32. Anti-cheat: do NOT subscribe to state/*.
    mqtt.subscribe(config.TOPIC_POWER_CMD, qos=1)

    last_poll = time.ticks_ms() if _MICROPY else int(time.monotonic() * 1000)

    def now_ms():
        return time.ticks_ms() if _MICROPY else int(time.monotonic() * 1000)

    def elapsed(now, since):
        return time.ticks_diff(now, since) if _MICROPY else now - since

    while True:
        now = now_ms()
        if elapsed(now, last_poll) >= config.POLL_INTERVAL_MS:
            res = fetch_power()
            if res is not None:
                ctrl.tick(res[0], res[1])
            last_poll = now
        mqtt.check_msg()
        time.sleep(0.05)


def run():
    # A device in a cupboard must recover, not drop to the REPL.
    try:
        main()
    except KeyboardInterrupt:
        raise
    except Exception as e:
        print("[main] fatal:", e)
        if _MICROPY:
            import machine
            time.sleep(5)
            machine.reset()
        else:
            raise


run()
