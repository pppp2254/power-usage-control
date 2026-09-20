# Controller behavior tests (Section 8 of CLAUDE.md), run on desktop CPython.
BASE = 150.0  # base household load reported by the meter
POLL = 2000


def warm(ctrl, clock):
    """Advance past MIN_OFF so turn-on is allowed, seed prev_usage at night."""
    clock.advance(180000)
    ctrl.tick(0.0, BASE)  # night: surplus negative, nothing commanded


def test_ac_verify_survives_ramp_split_across_polls(env):
    ctrl, mqtt, clock = env
    warm(ctrl, clock)

    clock.advance(POLL)
    ctrl.tick(2000.0, BASE)  # big surplus -> AC + fan commanded
    assert len(mqtt.of("cmd/ac")) == 1
    assert len(mqtt.of("cmd/fan")) == 1

    # Mid-ramp poll: AC at ~600 W, fan already on. Per-poll delta matches
    # nothing; cumulative-baseline verify must simply keep waiting.
    clock.advance(POLL)
    ctrl.tick(2000.0, BASE + 600 + 48)
    assert ctrl.ac.observed == "off"

    # Ramp complete: cumulative delta = +1200 (AC) and, after folding the AC
    # draw into the fan's baseline, +48 (fan).
    clock.advance(POLL)
    ctrl.tick(2000.0, BASE + 1200 + 48)
    assert ctrl.ac.observed == "on"
    assert ctrl.fan.observed == "on"
    assert len(mqtt.of("cmd/ac")) == 1  # no spurious resends
    assert len(mqtt.of("cmd/fan")) == 1


def test_lost_command_resends_then_backs_off(env):
    ctrl, mqtt, clock = env
    warm(ctrl, clock)

    clock.advance(POLL)
    ctrl.tick(250.0, BASE)  # surplus 100: fan only
    assert len(mqtt.of("cmd/fan")) == 1
    assert len(mqtt.of("cmd/ac")) == 0

    # Appliance is dead: usage never changes. Expect MAX_RESENDS then give-up.
    for _ in range(20):  # 40 s
        clock.advance(POLL)
        ctrl.tick(250.0, BASE)
    assert len(mqtt.of("cmd/fan")) == 1 + 3
    assert ctrl.fan.observed == "off"
    assert ctrl.fan.desired == "on"

    # Back-off: no command storm while RETRY_BACKOFF (60 s) is running.
    for _ in range(25):  # +50 s, still inside back-off
        clock.advance(POLL)
        ctrl.tick(250.0, BASE)
    assert len(mqtt.of("cmd/fan")) == 4

    # After back-off expires, exactly one fresh attempt cycle starts.
    for _ in range(10):
        clock.advance(POLL)
        ctrl.tick(250.0, BASE)
        if len(mqtt.of("cmd/fan")) == 5:
            break
    assert len(mqtt.of("cmd/fan")) == 5


def test_lost_fan_command_does_not_self_verify(env):
    # Regression: with a global 150 W tolerance, |0 - 48| <= 150 "verified"
    # a fan command that never happened.
    ctrl, mqtt, clock = env
    warm(ctrl, clock)
    clock.advance(POLL)
    ctrl.tick(250.0, BASE)
    clock.advance(POLL)
    ctrl.tick(250.0, BASE)  # no usage change
    assert ctrl.fan.observed == "off"


def test_user_override_adopted_not_fought(env):
    ctrl, mqtt, clock = env
    warm(ctrl, clock)

    clock.advance(POLL)
    ctrl.tick(0.0, BASE)  # idle night
    # User turns the fan on with their remote: +48 W appears at the meter.
    clock.advance(POLL)
    ctrl.tick(0.0, BASE + 48)

    assert ctrl.fan.observed == "on"
    assert ctrl.fan.desired == "on"  # adopted the user's choice
    assert mqtt.of("cmd/fan") == []  # crucially: no counter-command
    assert ctrl.override_remaining_ms() is not None

    # Grace expires -> auto resumes and turns it off (surplus is negative).
    clock.advance(600000)
    ctrl.tick(0.0, BASE + 48)
    assert ctrl.override_remaining_ms() is None
    cmds = mqtt.of("cmd/fan")
    assert len(cmds) == 1
    assert cmds[0]["power"] == "off"
    assert cmds[0]["source"] == "esp32"


def test_ac_hysteresis_no_chatter(env):
    ctrl, mqtt, clock = env
    warm(ctrl, clock)

    clock.advance(POLL)
    ctrl.tick(2000.0, BASE)  # AC + fan commanded
    clock.advance(POLL)
    ctrl.tick(2000.0, BASE + 1200 + 48)  # both verify
    assert ctrl.ac.observed == "on"
    on_usage = BASE + 1200 + 48

    # Thresholds are headroom with the AC off, so compare against the usage
    # that excludes its 1200 W: base + fan.
    without_ac = on_usage - 1200

    # Headroom inside the band (200..600): AC must stay on.
    for _ in range(5):
        clock.advance(POLL)
        ctrl.tick(without_ac + 400, on_usage)
    assert len(mqtt.of("cmd/ac")) == 1

    # Headroom below the off threshold, after MIN_ON: AC turns off.
    clock.advance(60000)
    ctrl.tick(without_ac + 100, on_usage)
    assert len(mqtt.of("cmd/ac")) == 2
    assert mqtt.of("cmd/ac")[1]["power"] == "off"

    # Off verifies; headroom is now huge, but MIN_OFF must block re-on (chatter).
    clock.advance(POLL)
    ctrl.tick(without_ac + 2000, without_ac)
    assert ctrl.ac.observed == "off"
    for _ in range(5):
        clock.advance(POLL)
        ctrl.tick(without_ac + 2000, without_ac)
    assert len(mqtt.of("cmd/ac")) == 2


def test_manual_mode_respects_compressor_min_off(env):
    ctrl, mqtt, clock = env
    ctrl.tick(0.0, BASE)  # seed usage

    ctrl.on_cmd_esp32("cmd/esp32", {"mode": "manual", "ac": "on", "fan": "on"})
    assert mqtt.of("cmd/ac") == []          # blocked: MIN_OFF since boot
    assert len(mqtt.of("cmd/fan")) == 1     # fan has no compressor

    clock.advance(180000)
    ctrl.on_cmd_esp32("cmd/esp32", {"mode": None, "ac": "on", "fan": None})
    assert len(mqtt.of("cmd/ac")) == 1
    assert mqtt.of("cmd/ac")[0]["power"] == "on"


def test_manual_commands_ignored_in_auto(env):
    ctrl, mqtt, clock = env
    warm(ctrl, clock)
    ctrl.on_cmd_esp32("cmd/esp32", {"mode": None, "ac": "on", "fan": "on"})
    assert mqtt.of("cmd/ac") == []
    assert mqtt.of("cmd/fan") == []


def test_ac_does_not_cycle_under_steady_sun(env):
    # Regression: thresholds used to be compared against the raw surplus, so
    # ON (measured with the AC off) and OFF (measured with it on) were in
    # different frames. At any input between base+600 and base+1400 the AC
    # turned itself on and off forever on the MIN_ON/MIN_OFF clock.
    ctrl, mqtt, clock = env
    clock.advance(180000)
    ctrl.tick(900.0, BASE)

    usage = BASE
    for _ in range(400):  # 800 s of perfectly steady 900 W sun
        clock.advance(POLL)
        ctrl.tick(900.0, usage)
        usage = BASE
        usage += 1200 if ctrl.ac.commanded == "on" else 0
        usage += 48 if ctrl.fan.commanded == "on" else 0

    powers = [c["power"] for c in mqtt.of("cmd/ac")]
    assert powers == ["on"], f"AC cycled: {powers}"


def test_solar_outage_keeps_telemetry_and_sheds_load(env):
    ctrl, mqtt, clock = env
    published = []
    ctrl._publish_telemetry = lambda: published.append(ctrl.solar_ok)

    clock.advance(180000)
    ctrl.tick(2000.0, BASE)                       # AC + fan commanded
    clock.advance(POLL)
    ctrl.tick(2000.0, BASE + 1200 + 48)           # both verify on
    assert ctrl.ac.observed == "on"
    clock.advance(60000)                          # clear MIN_ON

    before = len(published)
    for n in range(1, 5):                         # below MAX_POLL_FAILURES
        ctrl.on_poll_failure(n)
    assert len(published) == before + 4           # telemetry still flowing
    assert ctrl.solar_ok is False
    assert len(mqtt.of("cmd/ac")) == 1            # not yet shed

    ctrl.on_poll_failure(5)
    assert mqtt.of("cmd/ac")[-1]["power"] == "off"
    assert mqtt.of("cmd/fan")[-1]["power"] == "off"

    ctrl.tick(2000.0, BASE + 1200 + 48)
    assert ctrl.solar_ok is True                  # recovers on the next good read


def test_unresponsive_appliance_raises_fault(env):
    ctrl, mqtt, clock = env
    clock.advance(180000)
    ctrl.tick(250.0, BASE)                        # surplus 100: fan only
    assert ctrl.fan.fault is False

    for _ in range(20):                           # fan never moves the meter
        clock.advance(POLL)
        ctrl.tick(250.0, BASE)
    assert ctrl.fan.fault is True
    assert ctrl.ac.fault is False

    # The meter finally moves: the fault clears on proof of life.
    clock.advance(60000 + POLL)
    ctrl.tick(250.0, BASE)
    clock.advance(POLL)
    ctrl.tick(250.0, BASE + 48)
    assert ctrl.fan.observed == "on"
    assert ctrl.fan.fault is False


def test_boot_asserts_off_so_a_running_appliance_is_controllable(env):
    # Regression: `observed` starts at "off" as an assumption. If the fan is
    # really running, desired == observed == "off" makes _actuate skip it, so
    # auto mode can never switch off the load it was built to switch off.
    ctrl, mqtt, clock = env
    ctrl.assert_known_state()

    assert [c["power"] for c in mqtt.of("cmd/ac")] == ["off"]
    assert [c["power"] for c in mqtt.of("cmd/fan")] == ["off"]

    # Having been told off, the fan is genuinely off, so the meter's 150 W is
    # the base load and the next surplus decision starts from solid ground.
    clock.advance(180000)
    ctrl.tick(250.0, BASE)
    assert mqtt.of("cmd/fan")[-1]["power"] == "on"
