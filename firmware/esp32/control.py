# Control loop — Section 8 of CLAUDE.md.
# State is inferred from usage_w only. NEVER subscribe to state/ac or state/fan.
#
# Verification strategy: when a command is sent, snapshot usage_w as a baseline
# and verify against the CUMULATIVE change (usage_now - baseline), not the
# poll-to-poll delta. This survives the AC's startup ramp being split across
# multiple polls. When one appliance's command verifies while another is still
# pending, the verified draw is folded into the other's baseline.
import config

try:
    # MicroPython. NEVER do raw arithmetic on ticks values — they wrap.
    from time import ticks_ms, ticks_add, ticks_diff
except ImportError:
    # CPython fallback (desktop tests).
    import time as _time

    def ticks_ms():
        return int(_time.monotonic() * 1000)

    def ticks_add(t, delta):
        return t + delta

    def ticks_diff(a, b):
        return a - b


class Appliance:
    def __init__(self, name, draw_w, delta_tol_w, on_surplus, off_surplus,
                 cmd_topic, build_cmd, protect_min_off=False):
        self.name = name
        self.draw_w = draw_w
        # Tolerance must be < draw_w / 2 or verify/override matching is meaningless.
        self.delta_tol_w = delta_tol_w
        self.on_surplus = on_surplus
        self.off_surplus = off_surplus
        self.cmd_topic = cmd_topic
        self._build_cmd = build_cmd  # fn(power) -> dict
        self.protect_min_off = protect_min_off  # compressor: block fast restart even in manual

        self.desired = "off"
        self.commanded = "off"
        self.observed = "off"
        self.last_state_change_ms = ticks_ms()

        # Verify-after-send (cumulative vs baseline). None = nothing pending.
        self.pending_target = None       # "on" | "off" | None
        self.pending_deadline = None     # ticks
        self.baseline_usage_w = None     # usage_w snapshot at send time
        self.resends_left = 0
        self.retry_after = None          # ticks; back-off after verify give-up

        # Override. None = no override (never use 0 as sentinel: ticks wrap).
        self.override_until = None       # ticks

    def in_override(self, now):
        return self.override_until is not None and ticks_diff(self.override_until, now) > 0

    def can_turn_on(self, now):
        return self.observed == "off" and ticks_diff(now, self.last_state_change_ms) >= config.MIN_OFF_MS

    def can_turn_off(self, now):
        return self.observed == "on" and ticks_diff(now, self.last_state_change_ms) >= config.MIN_ON_MS

    def build_cmd(self, power):
        return self._build_cmd(power)


def build_ac_cmd(power):
    return {"source": "esp32", "power": power, "temp_c": 25, "fan_speed": "low"}


def build_fan_cmd(power):
    return {"source": "esp32", "power": power, "speed": "med"}


class Controller:
    def __init__(self, mqtt, publish_telemetry_fn):
        self._mqtt = mqtt
        self._publish_telemetry = publish_telemetry_fn

        self.mode = "auto"            # "auto" | "manual"
        self.prev_usage_w = None
        self.last_input_w = 0.0
        self.last_usage_w = 0.0

        self.ac = Appliance(
            "ac",
            config.AC_DRAW_W,
            config.AC_DELTA_TOL_W,
            config.AC_ON_SURPLUS_W,
            config.AC_OFF_SURPLUS_W,
            config.TOPIC_AC_CMD,
            build_ac_cmd,
            protect_min_off=True,
        )
        self.fan = Appliance(
            "fan",
            config.FAN_DRAW_W,
            config.FAN_DELTA_TOL_W,
            config.FAN_ON_SURPLUS_W,
            config.FAN_OFF_SURPLUS_W,
            config.TOPIC_FAN_CMD,
            build_fan_cmd,
        )

    # ---- Public ----

    def on_cmd_esp32(self, topic, msg):
        if topic != config.TOPIC_POWER_CMD or not isinstance(msg, dict):
            return
        new_mode = msg.get("mode")
        if new_mode in ("auto", "manual") and new_mode != self.mode:
            print("[ctrl] mode ->", new_mode)
            self.mode = new_mode

        if self.mode == "manual":
            now = ticks_ms()
            for app, key in ((self.ac, "ac"), (self.fan, "fan")):
                val = msg.get(key)
                if val not in ("on", "off"):
                    continue
                # Compressor protection holds even in manual: a user (or a buggy
                # client) must not short-cycle the AC.
                if val == "on" and app.protect_min_off and not app.can_turn_on(now):
                    print("[ctrl] manual", app.name, "on blocked (MIN_OFF protection)")
                    continue
                self._send_cmd(app, val, now)

    def tick(self, input_w, usage_w):
        now = ticks_ms()
        self.last_input_w = input_w
        self.last_usage_w = usage_w

        self._expire_timers(now)

        # Snapshot BEFORE verify: a verify that completes this tick must not let
        # its own usage jump be re-interpreted as a user override below.
        had_pending = (self.ac.pending_target is not None) or (self.fan.pending_target is not None)
        self._verify(usage_w, now)
        if not had_pending and self.prev_usage_w is not None:
            self._detect_override(usage_w - self.prev_usage_w, now)
        self.prev_usage_w = usage_w

        if self.mode == "auto":
            self._auto_decide(input_w, usage_w, now)

        self._actuate(now)
        self._publish_telemetry()

    def override_remaining_ms(self):
        """Longest remaining override across appliances, or None."""
        now = ticks_ms()
        rem = None
        for app in (self.ac, self.fan):
            if app.override_until is not None:
                d = ticks_diff(app.override_until, now)
                if d > 0 and (rem is None or d > rem):
                    rem = d
        return rem

    # ---- Internals ----

    def _expire_timers(self, now):
        # Clear expired ticks-based timers so stale values can't wrap into the future.
        for app in (self.ac, self.fan):
            if app.override_until is not None and ticks_diff(app.override_until, now) <= 0:
                app.override_until = None
            if app.retry_after is not None and ticks_diff(app.retry_after, now) <= 0:
                app.retry_after = None

    def _verify(self, usage_w, now):
        for app in (self.ac, self.fan):
            if app.pending_target is None:
                continue
            expected = app.draw_w if app.pending_target == "on" else -app.draw_w
            cum = usage_w - app.baseline_usage_w
            if abs(cum - expected) <= app.delta_tol_w:
                app.observed = app.pending_target
                app.last_state_change_ms = now
                self._clear_pending(app)
                app.retry_after = None
                print("[ctrl] verified", app.name, "->", app.observed)
                # Our verified draw is now part of usage_w; fold it into the
                # other appliance's baseline so its cumulative delta stays clean.
                other = self.fan if app is self.ac else self.ac
                if other.pending_target is not None:
                    other.baseline_usage_w += expected
            elif ticks_diff(now, app.pending_deadline) > 0:
                if app.resends_left > 0:
                    print("[ctrl] resend", app.name, "->", app.pending_target,
                          "(left=", app.resends_left, ")")
                    self._send_raw(app, app.pending_target)
                    app.resends_left -= 1
                    app.pending_deadline = ticks_add(now, config.VERIFY_TIMEOUT_MS)
                else:
                    print("[ctrl] giveup", app.name, "->", app.pending_target,
                          "(retry in", config.RETRY_BACKOFF_MS // 1000, "s)")
                    self._clear_pending(app)
                    app.retry_after = ticks_add(now, config.RETRY_BACKOFF_MS)

    def _clear_pending(self, app):
        app.pending_target = None
        app.pending_deadline = None
        app.baseline_usage_w = None
        app.resends_left = 0

    def _detect_override(self, delta, now):
        # Look for an unexpected usage delta matching one appliance flipping.
        # (If the user flips both within one poll the deltas merge and neither
        # matches — accepted limitation of meter-only inference.)
        for app in (self.ac, self.fan):
            if app.observed == "off" and abs(delta - app.draw_w) <= app.delta_tol_w:
                app.observed = "on"
            elif app.observed == "on" and abs(delta + app.draw_w) <= app.delta_tol_w:
                app.observed = "off"
            else:
                continue
            app.last_state_change_ms = now
            # Adopt the user's choice — never counter-command an override.
            app.desired = app.observed
            app.override_until = ticks_add(now, config.OVERRIDE_GRACE_MS)
            print("[ctrl] override detected:", app.name, "->", app.observed, "(user)")
            return

    def _auto_decide(self, input_w, usage_w, now):
        surplus = input_w - usage_w

        for app in (self.ac, self.fan):
            if app.in_override(now) and not config.AUTO_REASSERT:
                continue

            if app.observed == "off":
                if surplus >= app.on_surplus and app.can_turn_on(now):
                    app.desired = "on"
                else:
                    app.desired = "off"
            else:
                # surplus already excludes this appliance's draw (it is in usage_w),
                # so the off threshold compares remaining surplus directly.
                if surplus <= app.off_surplus and app.can_turn_off(now):
                    app.desired = "off"
                else:
                    app.desired = "on"

    def _actuate(self, now):
        for app in (self.ac, self.fan):
            if app.desired == app.observed:
                continue
            if app.pending_target is not None:
                continue  # verify in flight; wait for it to resolve
            if app.retry_after is not None:
                continue  # backing off after verify give-up
            if app.desired == "on" and not app.can_turn_on(now):
                continue
            if app.desired == "off" and not app.can_turn_off(now):
                continue
            self._send_cmd(app, app.desired, now)

    def _send_cmd(self, app, power, now):
        app.desired = power
        self._send_raw(app, power)
        if power == app.observed:
            return  # no usage change expected; nothing to verify
        app.baseline_usage_w = self.last_usage_w
        app.pending_target = power
        app.pending_deadline = ticks_add(now, config.VERIFY_TIMEOUT_MS)
        app.resends_left = config.MAX_RESENDS

    def _send_raw(self, app, power):
        payload = app.build_cmd(power)
        self._mqtt.publish(app.cmd_topic, payload, qos=1, retain=False)
        app.commanded = power
        print("[ctrl] cmd", app.name, "->", power)
