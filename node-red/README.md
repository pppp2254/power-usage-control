# Node-RED flows

`flows.json` exports the Solar Load Controller flow. Import via the Node-RED UI: `☰ → Import → select file`.

## Requirements

- Node-RED (>= 3.x)
- Install dashboard nodes: `npm i node-red-dashboard` in your Node-RED user dir, or via Palette Manager: `node-red-dashboard`.

## Configuration

The broker config node points at `localhost:1883` (Mosquitto). For FlowFuse cloud:
- Edit the `mqtt.broker.local` node → host `broker.flowfuse.cloud`, port `8883`, TLS on, set Security → username (`user@<teamid>`), password, client ID equal to username.
- The provisioned client must have publish/subscribe ACLs matching `docs/contracts.md` (Section 7).

## What the flow does

- subscribes `telemetry/esp32` (QoS 1, retained → fresh on import)
- appends each telemetry message as JSON line to `/data/telemetry.log`
- drives dashboard: gauges for `input_w` / `usage_w` / `available_w`, text for `mode` / `ac` / `fan`, power chart
- mode switch publishes `{ mode, ac: null, fan: null }` to `cmd/esp32`
- override alert: logs to debug pane while `override_until` is in the future

## FlowFuse

Inside FlowFuse, the Team Broker auto-provisions clients per flow. The FlowFuse MQTT in/out nodes can replace the standard MQTT nodes for zero-config; topic table stays identical.

Mind the Team plan **5-client cap** (ESP32 + web app + 3 mocks already = 5). For cloud demos either:
- Keep mocks on local Mosquitto, bridge only ESP32 + web app to FlowFuse, or
- Consolidate the 3 mocks behind one MQTT connection.
