# Copy to config.py and fill in. config.py is gitignored.

# --- WiFi ---
WIFI_SSID = "your-ssid"
WIFI_PASSWORD = "your-password"

# --- Solar API ---
SOLAR_URL = "http://192.168.1.50:3001/power"   # set to host running solar-mock

# --- MQTT broker ---
MQTT_HOST = "192.168.1.50"   # mosquitto local, or e.g. broker.flowfuse.cloud
MQTT_PORT = 1883
MQTT_USERNAME = None         # FlowFuse: "user@<teamid>" (also use as MQTT_CLIENT_ID)
MQTT_PASSWORD = None
MQTT_CLIENT_ID = "esp32-solar-01"   # must equal MQTT_USERNAME on FlowFuse
MQTT_TLS = False             # True for FlowFuse cloud

# --- Topics (locked by docs/contracts.md) ---
TOPIC_POWER_TELEMETRY = "telemetry/esp32"
TOPIC_POWER_CMD = "cmd/esp32"
TOPIC_AC_CMD = "cmd/ac"
TOPIC_FAN_CMD = "cmd/fan"
# DO NOT subscribe to state/ac or state/fan — anti-cheat rule (§2.5).

# --- Control tunables (§8) ---
POLL_INTERVAL_MS = 2000        # >= solar API update interval
AC_ON_SURPLUS_W = 600          # turn-on threshold (hysteresis)
AC_OFF_SURPLUS_W = 200         # turn-off threshold; gap prevents chattering
FAN_ON_SURPLUS_W = 50
FAN_OFF_SURPLUS_W = -50        # fan stays on across wide surplus band
MIN_ON_MS = 60000              # min on time before allowed off
MIN_OFF_MS = 180000            # min off time before allowed on (AC compressor)
OVERRIDE_GRACE_MS = 600000     # user wins for 10 min after manual override
VERIFY_TIMEOUT_MS = 6000       # wait for cumulative usage_w change to confirm a command
                               # (must cover AC ramp + one poll of jitter)
MAX_RESENDS = 3
RETRY_BACKOFF_MS = 60000       # after verify give-up, wait before trying again
# Expected appliance draws (used for verify + override inference).
AC_DRAW_W = 1200
FAN_DRAW_W = 48                # fan-mock draws base 60 W x speed gain; we command "med" (x0.8)
# Per-appliance tolerance for matching usage_w changes against expected draw.
# MUST be < draw/2, or a lost command "verifies" on zero change and user
# overrides are never detected.
AC_DELTA_TOL_W = 300
FAN_DELTA_TOL_W = 25

# --- Auto-mode re-assert policy (§8 explicit decision) ---
# False = user always wins until OVERRIDE_GRACE expires, then auto resumes.
# True  = auto may re-assert (e.g. cut AC) when surplus is critically low even during override.
AUTO_REASSERT = False
