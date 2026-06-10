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

    # Surplus falls into the hysteresis band (200..600): AC must stay on.
    for _ in range(5):
        clock.advance(POLL)
        ctrl.tick(on_usage + 400, on_usage)
    assert len(mqtt.of("cmd/ac")) == 1

    # Below the off threshold, after MIN_ON: AC turns off.
    clock.advance(60000)
    ctrl.tick(on_usage + 100, on_usage)
    assert len(mqtt.of("cmd/ac")) == 2
    assert mqtt.of("cmd/ac")[1]["power"] == "off"

    # Off verifies; surplus is now huge, but MIN_OFF must block re-on (chatter).
    clock.advance(POLL)
    off_usage = on_usage - 1200
    ctrl.tick(on_usage + 100, off_usage)
    assert ctrl.ac.observed == "off"
    for _ in range(5):
        clock.advance(POLL)
        ctrl.tick(on_usage + 100, off_usage)
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
