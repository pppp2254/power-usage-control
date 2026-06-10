# Solar-Aware Load Controller

Auto on/off control of AC + fan based on live solar input/usage. ESP32 holds control logic; mocks simulate panel/meter/appliances; web app monitors + switches auto/manual; Node-RED logs/dashboards.

See [`CLAUDE.md`](CLAUDE.md) for full spec. Contracts mirrored in [`docs/contracts.md`](docs/contracts.md).

## Quick start (local dev)

```bash
# 1. broker + mocks + web app (compose runs bun install automatically)
docker compose up -d
# open http://localhost:3000

# (or run any service outside docker:)
cd packages/web-app && bun install && bun run dev

# 2. ESP32 firmware
# copy firmware/esp32/config.example.py -> config.py, fill WiFi/MQTT
# flash MicroPython, upload files
```

### Testing at night

The solar mock follows the real clock (no sun after 18:00). Freeze it at solar noon:

```bash
curl -X POST localhost:3001/clock -H 'content-type: application/json' -d '{"fixed_hour": 12}'
# back to real clock:
curl -X POST localhost:3001/clock -H 'content-type: application/json' -d '{"fixed_hour": null}'
```

## Tests

Controller logic (verify-after-send, override detection, hysteresis, dwell timers)
runs on desktop CPython:

```bash
pip install pytest
python3 -m pytest tests/ -q
```

## Layout

```
packages/
  solar-mock/   panel + meter, REST GET /power, MQTT subscriber for state/*
  ac-mock/      cmd/ac -> state/ac
  fan-mock/     cmd/fan -> state/fan
  web-app/      Bun + Elysia + browser, MQTT<->WS bridge
firmware/esp32/ MicroPython control loop
node-red/       flows.json (logging + dashboard)
docs/           contracts mirror
mosquitto/      broker config (TCP 1883 + WS 9001)
```

## Ports (local)

| Service        | Port |
|----------------|------|
| Mosquitto MQTT | 1883 |
| Mosquitto WS   | 9001 |
| solar-mock     | 3001 |
| web-app        | 3000 |
| Node-RED       | 1880 |
