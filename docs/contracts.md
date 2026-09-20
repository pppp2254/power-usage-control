# Contracts

Single source of truth for the API + MQTT wire format. Mirrors §6–§7 of [`CLAUDE.md`](../CLAUDE.md). If you edit one, edit both.

Units: power in **watts (W)**. Timestamps in **epoch milliseconds (ms)**.

---

## Solar API (read-only)

Base URL: `http://localhost:3001` (configurable).

### `GET /power`

Only endpoint the ESP32 depends on.

```json
{
  "ts": 1717500000000,
  "input_w": 420.5,
  "usage_w": 310.0
}
```

| Field     | Type   | Notes |
|-----------|--------|-------|
| `ts`      | number | epoch ms |
| `input_w` | number | current solar input |
| `usage_w` | number | metered total consumption (incl. AC + fan + base load) |

### Mock-only dev endpoints (NOT real contract)

- `GET /scenario` → `{ "name": "sunny" | "cloudy" | "night" }`
- `POST /scenario` body `{ "name": "sunny" | "cloudy" | "night" }`

Gated under `/dev/*` would be cleaner; keep at root for now but mark clearly in the mock that they are not part of the real API.

---

## MQTT topics

| Topic              | Publisher           | Subscriber(s)            | QoS | Retain | Payload         |
|--------------------|---------------------|--------------------------|-----|--------|-----------------|
| `telemetry/esp32`  | ESP32               | web app, Node-RED        | 1   | yes    | telemetry obj   |
| `cmd/esp32`        | web app             | ESP32                    | 1   | no     | command obj     |
| `cmd/ac`           | ESP32, web app/user | AC mock                  | 1   | no     | AC IR-like cmd  |
| `cmd/fan`          | ESP32, web app/user | fan mock                 | 1   | no     | fan IR-like cmd |
| `state/ac`         | AC mock             | **solar mock only**      | 1   | yes    | appliance state |
| `state/fan`        | fan mock            | **solar mock only**      | 1   | yes    | appliance state |

> **Anti-cheat rule (§2.5):** ESP32 must NOT subscribe to `state/ac` / `state/fan`. Those topics exist so the solar mock can act as a meter. ESP32 infers state from `usage_w`.

### `telemetry/esp32`

```json
{
  "ts": 1717500000000,
  "input_w": 420.5,
  "usage_w": 310.0,
  "available_w": 110.5,
  "mode": "auto",
  "solar_ok": true,
  "ac":  { "desired": "off", "commanded": "off", "observed": "off", "fault": false },
  "fan": { "desired": "on",  "commanded": "on",  "observed": "on",  "fault": false },
  "override_until": null
}
```

`solar_ok` is `false` while `GET /power` is failing: `input_w`/`usage_w` are then the last good read rather than live values, and after `MAX_POLL_FAILURES` consecutive failures auto mode sheds load.

`fault` is `true` once the ESP32 has resent a command `MAX_RESENDS` times without the meter moving — the appliance stopped answering its remote, so `observed` beside it is a stale belief rather than a reading. It clears as soon as the meter confirms that appliance moving again.

`override_until` is `null` or epoch ms when the override grace expires. Per-appliance overrides may be represented in this object too if needed (e.g. `ac.override_until`); the top-level field is the simplest form for the web app.

### `cmd/esp32`

```json
{
  "mode": "auto",
  "ac":  null,
  "fan": "on"
}
```

| Field  | Values                              | Effect |
|--------|-------------------------------------|--------|
| `mode` | `"auto"` \| `"manual"` \| `null`    | `null` = leave unchanged |
| `ac`   | `"on"` \| `"off"` \| `null`         | only honored in `manual` mode |
| `fan`  | `"on"` \| `"off"` \| `null`         | only honored in `manual` mode |

### `cmd/ac` (full IR-like state)

```json
{
  "source": "esp32",
  "power": "on",
  "temp_c": 25,
  "fan_speed": "low"
}
```

| Field       | Values                        |
|-------------|-------------------------------|
| `source`    | `"esp32"` \| `"user"`         |
| `power`     | `"on"` \| `"off"`             |
| `temp_c`    | number                        |
| `fan_speed` | `"low"` \| `"med"` \| `"high"`|

### `cmd/fan`

```json
{ "source": "user", "power": "on", "speed": "med" }
```

| Field    | Values                         |
|----------|--------------------------------|
| `source` | `"esp32"` \| `"user"`          |
| `power`  | `"on"` \| `"off"`              |
| `speed`  | `"low"` \| `"med"` \| `"high"` (optional) |

### `state/ac` / `state/fan`

```json
{ "power": "on", "draw_w": 1200 }
```

| Field    | Values                  |
|----------|-------------------------|
| `power`  | `"on"` \| `"off"`       |
| `draw_w` | number, current draw    |

Retained: a fresh solar-mock startup gets the latest known appliance state immediately.
