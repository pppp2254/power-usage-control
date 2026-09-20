# Desktop test harness for the ESP32 controller.
# Injects a deterministic fake `config` module BEFORE importing control,
# so tests never depend on the developer's real config.py.
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "firmware", "esp32")))

_CFG = dict(
    TOPIC_POWER_TELEMETRY="telemetry/esp32",
    TOPIC_POWER_CMD="cmd/esp32",
    TOPIC_AC_CMD="cmd/ac",
    TOPIC_FAN_CMD="cmd/fan",
    POLL_INTERVAL_MS=2000,
    MAX_POLL_FAILURES=5,
    AC_ON_SURPLUS_W=600,
    AC_OFF_SURPLUS_W=200,
    FAN_ON_SURPLUS_W=50,
    FAN_OFF_SURPLUS_W=-50,
    MIN_ON_MS=60000,
    MIN_OFF_MS=180000,
    OVERRIDE_GRACE_MS=600000,
    VERIFY_TIMEOUT_MS=6000,
    MAX_RESENDS=3,
    RETRY_BACKOFF_MS=60000,
    AC_DRAW_W=1200,
    FAN_DRAW_W=48,
    AC_DELTA_TOL_W=300,
    FAN_DELTA_TOL_W=25,
    AUTO_REASSERT=False,
    ASSERT_OFF_AT_BOOT=True,
)

config = types.ModuleType("config")
for k, v in _CFG.items():
    setattr(config, k, v)
sys.modules["config"] = config

import control  # noqa: E402


class FakeClock:
    def __init__(self):
        self.t = 0

    def __call__(self):
        return self.t

    def advance(self, ms):
        self.t += ms


class FakeMqtt:
    def __init__(self):
        self.published = []  # (topic, payload)

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload))

    def of(self, topic):
        return [p for t, p in self.published if t == topic]


@pytest.fixture
def env(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(control, "ticks_ms", clock)
    mqtt = FakeMqtt()
    ctrl = control.Controller(mqtt, lambda: None)
    return ctrl, mqtt, clock
