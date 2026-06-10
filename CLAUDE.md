# Solar-Aware Load Controller — Project Foundation

> Foundation / spec document for building with Claude Code.
> Place this at the repo root (you can rename it `CLAUDE.md` so Claude Code reads it automatically), or keep it as `docs/FOUNDATION.md` and reference it.

## 1. Goal

Build a system that automatically turns an **AC** and a **fan** on/off to use power efficiently, based on live power data (input vs. usage) read from a **solar control system's API**. A user can still control the appliances manually at any time, and a **web app** lets them monitor everything and switch between automatic and manual control.

For development, everything except the control logic is **mocked** so the system can be built and tested without real hardware or weather.

## 2. Core design principles (read these first)

These decisions shape every component. Do not violate them.

1. **The ESP32 holds the control logic.** It decides what to turn on/off. The web app and Node-RED observe and command, but the decision loop lives on-device.
2. **The solar API is read-only.** The ESP32 (and everything else) may only *read* power data from the solar mock. Nothing writes power data back to it.
3. **State is inferred from the meter, not from the control path.** The appliances are controlled with one-way, "IR-like" commands (fire-and-forget) — there is no acknowledgment. The *real* on/off state is whatever the metered `usage_w` says. The ESP32 sends a command, then **verifies** by watching usage change.
4. **The meter only sees what's inside its boundary.** The mock must reflect this: `usage_w` includes the AC and fan draw. (In a real deployment you must confirm the appliances are electrically downstream of that meter — otherwise inference is impossible.)
5. **The ESP32 must NOT cheat.** It must not subscribe to the appliances' internal `state/*` topics to learn their state. Those topics exist only so the solar mock can act as a meter. The ESP32 learns state the same way it would in reality: through metered `usage_w`.
6. **The human is a first-class actor.** A user can command the appliances anytime, regardless of mode. The system observes this via the meter and does not fight the user (see override grace period).

## 3. Architecture overview

```
                       reads (HTTP GET, read-only)
  ┌──────────────────┐ ───────────────────────────► ┌──────────────┐
  │  Solar mock      │                                │   ESP32      │
  │  (panel + meter) │                                │ (MicroPython)│
  │  REST API        │                                │ control loop │
  └──────────────────┘                                └──────┬───────┘
        ▲  aggregates load draw                               │ MQTT
        │  (state/ac, state/fan)                              ▼
  ┌─────┴───────┐   ┌────────────┐                     ┌─────────────┐
  │  AC mock    │   │  Fan mock  │ ◄── "IR-like" cmds ──│ MQTT broker │
  └─────────────┘   └────────────┘   (cmd/ac, cmd/fan)  │ (Mosquitto  │
        ▲ user can also send cmd/ac, cmd/fan            │ → FlowFuse) │
        │ (manual remote)                               └─────┬───────┘
                                                              │
                                  ┌───────────────────────────┼────────────┐
                                  ▼                            ▼            │
                           ┌─────────────┐              ┌─────────────┐     │
                           │  Web app    │              │  Node-RED   │     │
                           │ Bun+Elysia  │              │ flows, logs │     │
                           │ + browser   │              │ (FlowFuse)  │     │
                           └─────────────┘              └─────────────┘     │
```

Data/feedback loop: ESP32 sends an IR-like command → appliance changes draw → solar mock's meter sees the new total `usage_w` → ESP32 reads it on the next poll → confirms (or re-sends).

## 4. Components

| # | Component | Stack | Responsibility |
|---|-----------|-------|----------------|
| 1 | Solar mock | Bun + Elysia (TypeScript) | Simulate solar input + act as the meter. Read-only REST API. |
| 2 | AC mock | Bun (TypeScript, MQTT client) | Simulate an AC responding to IR-like commands; report its draw. |
| 3 | Fan mock | Bun (TypeScript, MQTT client) | Simulate a fan responding to IR-like commands; report its draw. |
| 4 | ESP32 firmware | MicroPython | Poll solar API, run control logic, command appliances, publish telemetry over MQTT. |
| 5 | Web app | Bun + Elysia + browser | Display telemetry, switch auto/manual, send commands. Bridges MQTT ↔ browser over WebSocket. |
| 6 | Node-RED via FlowFuse | FlowFuse (Node-RED + Team Broker) | Hosted Node-RED, MQTT broker for the system, flows, logging/dashboard, integration. |

> Stack note: Bun/TypeScript is suggested for the mocks and web app for a single runtime. They could equally be Python (FastAPI) if preferred — keep the **contracts** (Section 6 & 7) identical regardless of language.

### 4.1 Solar mock (panel + meter)
- Generates `input_w` from a daily solar curve scaled by a **scenario** (`sunny`, `cloudy`, `night`), with mild noise.
- Acts as the **meter**: subscribes to `state/ac` and `state/fan`, sums their `draw_w` plus a configurable base household load → `usage_w`.
- Exposes the **read-only** power API (Section 6).
- Mock-only test controls let you switch scenario (these are NOT part of the real contract — clearly separate them).

### 4.2 AC mock
- Subscribes to `cmd/ac`. Accepts a full desired state (power/temp/fan_speed) — model real ACs that send complete state per IR press, not a toggle.
- When on, draws a configurable wattage (default ~1200 W) with a short startup ramp.
- Publishes `state/ac` (`power`, `draw_w`) whenever its state changes. (Consumed by the solar mock only.)

### 4.3 Fan mock
- Subscribes to `cmd/fan`. Simpler than the AC: `power` + optional `speed`.
- When on, draws a configurable wattage (default ~60 W).
- Publishes `state/fan`.

### 4.4 ESP32 firmware (MicroPython)
- Connects WiFi → MQTT broker (`umqtt.simple` or `umqtt.robust`). Use TLS when pointing at AWS IoT Core.
- Polls the solar API over HTTP (non-blocking-friendly: keep the poll short and time-sliced so MQTT stays responsive).
- Runs the control loop (Section 8).
- Subscribes `cmd/esp32` (from web app). Publishes `telemetry/esp32` (retained).
- Sends appliance commands on `cmd/ac` / `cmd/fan` (its "remote module").

### 4.5 Web app (Bun + Elysia)
- Elysia connects to the broker as an MQTT client (the `mqtt` npm package runs under Bun) and bridges to the browser over Elysia's WebSocket support.
- Browser UI: live readouts (`input_w`, `usage_w`, `available_w`, per-appliance desired/observed), a mode toggle (auto/manual), and manual on/off buttons.
- Publishes to `cmd/esp32`. Subscribes to `telemetry/esp32`.
- Optionally exposes a "manual remote" that publishes directly to `cmd/ac` / `cmd/fan` to simulate the user's physical remote.

### 4.6 Node-RED via FlowFuse
- **FlowFuse hosts Node-RED and provides the system's MQTT broker** (the Team Broker), so no separate broker service is needed in production. Cloud endpoint: `broker.flowfuse.cloud`.
- **Broker client limits matter**: Team plan = 5 clients, Enterprise = 20. The ESP32, web app, and three mocks already total 5 connections, so **develop locally against Mosquitto** and only point the integrated/cloud build at the FlowFuse broker. To fit the Team plan, either consolidate the three mocks behind fewer MQTT connections or keep the mocks on local Mosquitto and bridge only the device + app traffic to FlowFuse.
- **Provisioning a client**: create it in the FlowFuse Broker tab. The username gets the team id appended (e.g. `alice@<teamid>`), and the client **must use that same username string as its MQTT Client ID** or the connection is rejected. Set per-client publish/subscribe ACLs to match the topic table (Section 7).
- **ESP32 connects as a plain MQTT client** to the Team Broker with provisioned credentials + TLS. It does **not** use the FlowFuse Device Agent — that agent runs Node-RED on Linux boards (e.g. a Raspberry Pi), not on an ESP32.
- **Inside Node-RED**, use the FlowFuse MQTT nodes for zero-config broker access (the broker client is auto-created), or standard MQTT nodes pointed at the same broker. Flows handle telemetry logging, a dashboard, and alerting.
- FlowFuse Cloud runs on AWS underneath, so you get managed cloud infrastructure without setting up IoT Core, certificates, or EC2 yourself. (Self-hosting FlowFuse is also possible; its Team Broker requires an Enterprise license.)
- Keep all broker credentials out of the repo (Section 10).

## 5. Repository structure (suggested)

```
solar-load-controller/
  README.md
  CLAUDE.md                  # this foundation doc
  docker-compose.yml         # mosquitto + mock services for local dev
  packages/
    solar-mock/              # Bun + Elysia REST API (panel + meter)
    ac-mock/                 # Bun MQTT client
    fan-mock/                # Bun MQTT client
    web-app/                 # Bun + Elysia + frontend (MQTT↔WS bridge)
  firmware/
    esp32/                   # MicroPython: main.py, mqtt_client.py, control.py, config.py
  node-red/
    flows.json               # exported flows
    flowfuse/                # FlowFuse instance + broker client setup notes (no secrets)
  docs/
    contracts.md             # API + MQTT contracts (mirror of Sections 6–7)
    diagrams/
```

## 6. Solar API contract (read-only)

Base URL configurable (e.g. `http://localhost:3001`).

**`GET /power`** — the only endpoint the ESP32 depends on.
```json
{
  "ts": 1717500000000,   // epoch ms
  "input_w": 420.5,      // solar power currently available
  "usage_w": 310.0       // total metered consumption (includes AC + fan)
}
```

Mock-only test endpoints (NOT part of the real contract — gate behind a flag/path so they're obviously dev-only):
- `GET /scenario` → current scenario
- `POST /scenario` `{ "name": "sunny" | "cloudy" | "night" }`

> Confirm with the real solar system: the actual field names, units (W vs kW), and **update interval**. The update interval caps how fast the ESP32's verify-after-send loop can run.

## 7. MQTT topic & message contract

| Topic | Publisher | Subscriber(s) | QoS / retain | Payload |
|-------|-----------|---------------|--------------|---------|
| `telemetry/esp32` | ESP32 | web app, Node-RED | QoS 1, **retained** | telemetry object (below) |
| `cmd/esp32` | web app | ESP32 | QoS 1 | command object (below) |
| `cmd/ac` | ESP32, user/web app | AC mock | QoS 1 | IR-like AC command |
| `cmd/fan` | ESP32, user/web app | fan mock | QoS 1 | IR-like fan command |
| `state/ac` | AC mock | **solar mock only** | QoS 1, retained | `{ "power": "on"\|"off", "draw_w": 1200 }` |
| `state/fan` | fan mock | **solar mock only** | QoS 1, retained | `{ "power": "on"\|"off", "draw_w": 60 }` |

`telemetry/esp32`:
```json
{
  "ts": 1717500000000,
  "input_w": 420.5,
  "usage_w": 310.0,
  "available_w": 110.5,
  "mode": "auto",
  "ac":  { "desired": "off", "commanded": "off", "observed": "off" },
  "fan": { "desired": "on",  "commanded": "on",  "observed": "on" },
  "override_until": null
}
```

`cmd/esp32`:
```json
{
  "mode": "auto",            // "auto" | "manual" | null (null = leave unchanged)
  "ac": null,                // "on" | "off" | null  (only honored in manual mode)
  "fan": "on"
}
```

`cmd/ac` (full state, like a real IR press):
```json
{ "source": "esp32",         // "esp32" | "user"
  "power": "on",             // "on" | "off"
  "temp_c": 25,
  "fan_speed": "low" }
```

`cmd/fan`:
```json
{ "source": "user", "power": "on", "speed": "med" }
```

## 8. Control logic spec (ESP32)

State the firmware tracks per appliance: `desired`, `commanded` (last IR-like command sent), `observed` (inferred from meter), and `override_until` (timestamp).

Loop (runs every `POLL_INTERVAL`, sized to the API update interval):

1. **Read** `GET /power` → `input_w`, `usage_w`. Compute `surplus = input_w - usage_w`.
2. **Infer observed state** from `usage_w` changes vs. the last reading and known appliance draws (thresholds, not exact equality).
3. **Detect manual override**: if `observed` changed in a way the ESP32 did not command, set `override_until = now + OVERRIDE_GRACE` for that appliance, reflect it in telemetry, and skip auto control of it until the grace expires.
4. **If `mode == auto`** and no active override: choose `desired` states by priority + **hysteresis**:
   - Fan: cheap → may stay on across a wide surplus band.
   - AC: expensive → turn on only above `AC_ON_SURPLUS` (e.g. 600 W); turn off below `AC_OFF_SURPLUS` (e.g. 200 W). The gap prevents chattering.
   - Respect `MIN_ON` / `MIN_OFF` timers (model compressor protection, e.g. 180 s off before restart).
5. **Actuate**: for any appliance where `desired != observed`, publish the full state on `cmd/ac` / `cmd/fan`, set `commanded`, and start a verify timer.
6. **Verify-after-send**: if `usage_w` does not change by ~the appliance's expected draw within `VERIFY_TIMEOUT`, the command was likely missed → re-send (up to `MAX_RESENDS`). The meter both confirms and catches misses.
7. **Handle `cmd/esp32`**: apply mode switches; in manual mode, apply manual on/off directly (and send to the appliance).
8. **Publish** `telemetry/esp32` (retained).

Policy decision to make explicitly: in auto mode, may the system **re-assert** against the user (e.g. force the AC off when power is critically low), or does the user always win until they switch back to auto? Pick one and document it; it's the line between helpful and annoying.

Tunables (put in `config.py`): `POLL_INTERVAL`, `AC_ON_SURPLUS`, `AC_OFF_SURPLUS`, `MIN_ON`, `MIN_OFF`, `OVERRIDE_GRACE`, `VERIFY_TIMEOUT`, `MAX_RESENDS`, appliance draw estimates, base load.

## 9. Build order (milestones)

1. **Contracts** — finalize Sections 6 & 7 (this doc). Mirror into `docs/contracts.md`.
2. **Local broker** — Mosquitto via `docker-compose` (enable a WebSocket listener for the web app).
3. **Mocks** — solar + AC + fan. Verify: switching a load changes `usage_w` via the meter aggregation.
4. **ESP32 baseline** — WiFi + MQTT connect, poll API, publish telemetry. No control yet.
5. **Auto control + verify-after-send** — implement Section 8 steps 1–6.
6. **Web app** — telemetry display + mode toggle + manual commands.
7. **Manual override handling** — step 3, plus the re-assert policy you chose.
8. **Node-RED (local)** — logging + dashboard flows against Mosquitto.
9. **FlowFuse migration** — import the flows into a FlowFuse-hosted Node-RED instance; provision broker clients; swap the broker config (HOST/PORT/USERNAME/PASSWORD env vars, TLS) on the ESP32 and web app to point at `broker.flowfuse.cloud`. Mind the client limit (5 on Team).

Each milestone should be runnable and demoable on its own.

## 10. Conventions & config

- **Secrets/credentials** (WiFi, FlowFuse broker username/password, broker URLs) live in env files or `config.py` that are **git-ignored**. Provide `.env.example` / `config.example.py` with placeholders. Remember the broker client ID must equal the username.
- All timestamps are epoch milliseconds, all power values are watts (confirm against the real API).
- Topic names and JSON field names are defined here once; do not diverge between components.
- Prefer non-blocking patterns on the ESP32 so MQTT and HTTP polling coexist.
- Provide a `docker-compose.yml` that brings up the broker + three mocks for one-command local dev.

## 11. Open questions to resolve before/while building

- Real solar API: exact field names, units, and **update interval**?
- Are the AC and fan actually within the meter's boundary (so `usage_w` reflects them)?
- AC IR protocol/brand (for the real remote module via a library like `IRremoteESP8266`; in the mock this is just the `cmd/ac` payload).
- Re-assert policy in auto mode (Section 8).
- FlowFuse plan and **client budget**: does the build fit within 5 clients (Team), or do you need Enterprise (20) or consolidated mock connections?
