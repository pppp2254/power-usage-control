import { Elysia, t } from "elysia";
import { staticPlugin } from "@elysiajs/static";
import mqtt from "mqtt";

const MQTT_URL = process.env.MQTT_URL ?? "mqtt://localhost:1883";
const HTTP_PORT = Number(process.env.HTTP_PORT ?? 3000);
const MQTT_USERNAME = process.env.MQTT_USERNAME;
const MQTT_PASSWORD = process.env.MQTT_PASSWORD;

// Topics the browser may write to via the WS bridge.
// Whitelist: prevent arbitrary publish from clients.
const PUBLISH_WHITELIST = new Set(["cmd/esp32", "cmd/ac", "cmd/fan"]);

// Topics forwarded down to all browsers.
const FORWARD_TOPICS = ["telemetry/esp32"];

const mqttClientId =
  (MQTT_USERNAME ?? `web-app-${Math.random().toString(16).slice(2, 8)}`);

const client = mqtt.connect(MQTT_URL, {
  clientId: mqttClientId,
  username: MQTT_USERNAME,
  password: MQTT_PASSWORD,
  reconnectPeriod: 2000,
});

const subscribers = new Set<{ send: (data: string) => void }>();
let lastTelemetry: string | null = null;

client.on("connect", () => {
  console.log(`[web-app] mqtt connected: ${MQTT_URL}`);
  client.subscribe(FORWARD_TOPICS, { qos: 1 }, (err) => {
    if (err) console.error("[web-app] subscribe error:", err);
  });
});

client.on("error", (err) => console.error("[web-app] mqtt error:", err));

client.on("message", (topic, payload) => {
  const data = JSON.stringify({ type: "msg", topic, payload: tryParse(payload.toString()) });
  if (topic === "telemetry/esp32") lastTelemetry = data;
  for (const ws of subscribers) ws.send(data);
});

function tryParse(s: string): unknown {
  try {
    return JSON.parse(s);
  } catch {
    return s;
  }
}

// Server-side payload validation: the topic whitelist alone still lets any
// browser publish arbitrary JSON to cmd/* topics.
const POWERS = new Set(["on", "off"]);
const SPEEDS = new Set(["low", "med", "high"]);
const onOffOrNull = (v: unknown) => v == null || (typeof v === "string" && POWERS.has(v));

function validCmd(topic: string, p: unknown): boolean {
  if (typeof p !== "object" || p === null) return false;
  const o = p as Record<string, unknown>;
  switch (topic) {
    case "cmd/esp32":
      return (
        (o.mode == null || o.mode === "auto" || o.mode === "manual") &&
        onOffOrNull(o.ac) &&
        onOffOrNull(o.fan)
      );
    case "cmd/ac":
      return (
        POWERS.has(o.power as string) &&
        (o.temp_c == null || (typeof o.temp_c === "number" && o.temp_c >= 16 && o.temp_c <= 30)) &&
        (o.fan_speed == null || SPEEDS.has(o.fan_speed as string))
      );
    case "cmd/fan":
      return POWERS.has(o.power as string) && (o.speed == null || SPEEDS.has(o.speed as string));
    default:
      return false;
  }
}

new Elysia()
  .use(staticPlugin({ assets: "public", prefix: "/" }))
  .get("/health", () => ({ ok: true, mqtt_connected: client.connected }))
  .ws("/ws", {
    body: t.Object({
      type: t.Literal("publish"),
      topic: t.String(),
      payload: t.Unknown(),
    }),
    open(ws) {
      subscribers.add(ws);
      console.log(`[ws] client open. total=${subscribers.size}`);
      if (lastTelemetry) ws.send(lastTelemetry);
    },
    close(ws) {
      subscribers.delete(ws);
      console.log(`[ws] client close. total=${subscribers.size}`);
    },
    message(ws, msg) {
      if (msg.type !== "publish") return;
      if (!PUBLISH_WHITELIST.has(msg.topic)) {
        console.warn(`[ws] rejected publish to ${msg.topic}`);
        ws.send(JSON.stringify({ type: "error", error: `topic not allowed: ${msg.topic}` }));
        return;
      }
      const obj = typeof msg.payload === "string" ? tryParse(msg.payload) : msg.payload;
      if (!validCmd(msg.topic, obj)) {
        console.warn(`[ws] rejected invalid payload for ${msg.topic}:`, obj);
        ws.send(JSON.stringify({ type: "error", error: `invalid payload for ${msg.topic}` }));
        return;
      }
      const payload = JSON.stringify(obj);
      client.publish(msg.topic, payload, { qos: 1 }, (err) => {
        if (err) {
          console.error(`[ws] publish error on ${msg.topic}:`, err);
          ws.send(JSON.stringify({ type: "error", error: String(err) }));
        }
      });
    },
  })
  .listen(HTTP_PORT);

console.log(`[web-app] HTTP listening on http://localhost:${HTTP_PORT}`);
