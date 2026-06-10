# Code Review — 2026-06-10

Verdict: clean structure, contracts match `docs/contracts.md`, and the anti-cheat rule (§2.5) is correctly respected — the ESP32 only subscribes to `cmd/esp32`. But the verify/override inference has several real bugs that will break it on-device or even against the mocks.

> **Fix status (same day):** all Critical/High items fixed, plus most Medium/Low ones.
> Fixed: #1 (cumulative-baseline verify), #2 (per-appliance tolerances, `AC_DELTA_TOL_W`/`FAN_DELTA_TOL_W`), #3 (`ticks_add` + `None` sentinels), #4 (`RETRY_BACKOFF_MS` after give-up), #5 (NTP sync + 2000-epoch offset), #6 (`FAN_DRAW_W=48`), #7 (WiFi retry loop, fatal→`machine.reset()`, MQTT reconnect+resubscribe), #8 (`bun install` in compose + web-app service), manual MIN_OFF guard, dead code removed, override now *adopts* the user's state instead of fighting it (new bug found while fixing), solar-mock `/clock` fixed-hour control, WS payload validation, foundation doc deduped. Added `tests/` — 7 controller tests, all passing.
> Not done: AC ramp retained-message spam (harmless locally); WS auth (do before FlowFuse migration).

## Critical

### 1. Verify-after-send compares per-poll delta — broken by the AC ramp
`control.py:_verify_or_detect` checks `abs(delta - 1200) <= 150` where `delta` is usage change between two consecutive polls. The AC mock ramps 0→1200 W over `AC_RAMP_MS=2000`, equal to `POLL_INTERVAL_MS=2000`. Depending on phase, the ESP32 sees two ~600 W deltas instead of one 1200 W delta → verify never matches → resends exhaust → give-up. Result: AC is physically on but `observed` stays `"off"` forever, and `can_turn_off()` (requires `observed=="on"`) blocks any corrective action. Deadlock.

**Fix:** snapshot `usage_w` as a baseline when the command is sent and verify against the *cumulative* change (`usage_now - baseline_at_send`), not the poll-to-poll delta.

### 2. Fan verify and fan override detection are both meaningless
`DELTA_TOLERANCE_W=150` vs fan draw of 60 W (actually 48 W — see #6):

- Verify: `abs(delta - 60) <= 150` is true even when `delta == 0`. A lost fan command always "verifies".
- Override: `_detect_override` early-returns when `abs(delta) < 150`, so a user toggling the fan (±48–60 W) is **never** detected.

**Fix:** per-appliance tolerance, must be < draw/2 (e.g. AC 300, fan 25).

### 3. MicroPython ticks arithmetic is wrap-unsafe
`pending_until = ticks_ms() + VERIFY_TIMEOUT_MS` and `override_until_ms = now + OVERRIDE_GRACE_MS` (`control.py:136,220,165`). MicroPython docs forbid direct addition on ticks — use `time.ticks_add()`. Also `in_override()` does `ticks_diff(self.override_until_ms, now)` with the `0` sentinel for "never overridden"; after enough uptime the wrap makes this spuriously positive → appliance permanently treated as overridden. Use `None` as the sentinel and `ticks_add` everywhere.

## High

### 4. Give-up → resend storm
After `MAX_RESENDS`, `pending_target` clears but `desired != observed` persists, so `_actuate` re-sends on the very next tick, restarting the cycle every ~2 s forever. Add a back-off/fault state per appliance (e.g. retry no sooner than 60 s, surface `"fault"` in telemetry).

### 5. Telemetry timestamps are wrong on real hardware
`_epoch_ms()` uses `utime.time() * 1000`. On ESP32 that's seconds since **2000-01-01** (not Unix) and garbage until an NTP sync. Sync via `ntptime.settime()` at boot and add the 946684800 s offset. Same flaw inside `_epoch_ms_from_ticks`.

### 6. Expected fan draw doesn't match the fan mock
Firmware expects `FAN_DRAW_W=60`, but the mock draws `60 * SPEED_GAIN[speed]` and the ESP32 commands `speed: "med"` → 48 W. Harmless only because of bug #2; once tolerances are fixed this mismatch matters. Align the config or have the firmware command a speed whose draw it knows.

### 7. No crash recovery on-device
`boot.py` gives up after a 30 s WiFi timeout but proceeds; `main()` then throws on MQTT connect and the board drops to REPL. Wrap `main()` in try/except with `machine.reset()` (or retry loop). Also `mqtt.publish` in `Mqtt` is uncaught — one broker hiccup mid-`tick()` kills the loop.

### 8. `docker compose up` fails on a fresh clone
The mock services run `bun run --watch src/index.ts` on a bind-mounted dir with no `bun install` step. Without host-side `node_modules`, startup fails. Use `command: ["sh", "-c", "bun install && bun run --watch src/index.ts"]` or proper Dockerfiles. Also: `web-app` isn't in compose at all — add it for one-command dev.

## Medium

- **Late verification misread as user override** — if the usage delta lands *after* give-up, `_detect_override` attributes it to the user and locks auto out for 10 min. With fix #1 (baseline comparison) this mostly disappears; otherwise compare recent self-commands before declaring override.
- **Manual mode bypasses compressor protection** — `on_cmd_esp32` calls `_send_cmd` directly, skipping `MIN_ON/MIN_OFF`, while the comment in `_actuate` claims manual respects MIN_OFF. Pick one (suggest: enforce MIN_OFF for AC even in manual) and make code match comment.
- **Dead code in `_auto_decide`** — `effective_surplus_if_off` is computed, suppressed with `_ =`, never used. Either use it for the off-threshold (and document that `AC_OFF_SURPLUS` then means post-shutoff surplus) or delete it.
- **Blocking HTTP poll starves MQTT** — `urequests.get(timeout=3)` can block 3 s of a 2 s loop; `check_msg` and verify timing stall. Acceptable for v1; note it. Also confirm your MicroPython `urequests` build supports the `timeout` kwarg (older ones raise TypeError).
- **Solar mock untestable at night** — real-clock half-sine means `input_w=0` outside 06:00–18:00; you can't develop auto-control logic in the evening. Add a dev-only `TIME_SCALE`/fixed-input control next to `/scenario`.
- **WS bridge: no payload validation** — topic whitelist is good, but any browser on the LAN can publish arbitrary JSON to `cmd/*` (e.g. `temp_c: 9999`) with no auth. Fine for dev; validate payload schema server-side before FlowFuse migration.

## Low

- `CLAUDE.md` and `solar-load-controller-foundation.md` are byte-identical duplicates — keep one, or make the second a symlink/pointer, or they will diverge.
- AC ramp publishes 8 retained QoS 1 state messages per turn-on (every 250 ms) — harmless locally, wasteful on FlowFuse.
- `mqtt_client.py:now_ms()` is unused.
- No tests anywhere. `control.py` already runs under CPython (the `requests` fallback exists in `main.py`) — extract `ticks_*` behind an injectable clock and pytest the controller: verify-success, verify-timeout-resend, override-detect, hysteresis, dwell timers. This is the single highest-leverage improvement; bugs #1–#3 would all have been caught.

## What's done right

Topic/payload contracts match the spec exactly; read-only solar API honored; meter-boundary aggregation correct; mode/override semantics follow §8; web bridge whitelist; secrets properly gitignored with examples provided; FlowFuse client-ID-equals-username noted in config.
