import mqtt from "mqtt";

const MQTT_URL = process.env.MQTT_URL ?? "mqtt://localhost:1883";
const FAN_DRAW_W = Number(process.env.FAN_DRAW_W ?? 60);

type Power = "on" | "off";
type Speed = "low" | "med" | "high";

interface FanCmd {
  source?: "esp32" | "user";
  power: Power;
  speed?: Speed;
}

const state = {
  power: "off" as Power,
  speed: "low" as Speed,
  draw_w: 0,
};

const SPEED_GAIN: Record<Speed, number> = { low: 0.6, med: 0.8, high: 1.0 };

const client = mqtt.connect(MQTT_URL, {
  clientId: `fan-mock-${Math.random().toString(16).slice(2, 8)}`,
  reconnectPeriod: 2000,
});

client.on("connect", () => {
  console.log(`[fan-mock] connected: ${MQTT_URL}`);
  client.subscribe("cmd/fan", { qos: 1 }, (err) => {
    if (err) console.error("[fan-mock] subscribe error:", err);
  });
  publishState();
});

client.on("error", (err) => console.error("[fan-mock] mqtt error:", err));

client.on("message", (topic, payload) => {
  if (topic !== "cmd/fan") return;
  let cmd: FanCmd;
  try {
    cmd = JSON.parse(payload.toString()) as FanCmd;
  } catch (e) {
    console.warn("[fan-mock] bad json:", payload.toString());
    return;
  }
  if (cmd.power !== "on" && cmd.power !== "off") {
    console.warn("[fan-mock] missing/invalid power:", cmd);
    return;
  }
  state.power = cmd.power;
  if (cmd.speed) state.speed = cmd.speed;
  state.draw_w = state.power === "on" ? Math.round(FAN_DRAW_W * SPEED_GAIN[state.speed]) : 0;

  console.log(`[fan-mock] cmd from=${cmd.source ?? "?"} power=${state.power} speed=${state.speed} draw=${state.draw_w}W`);
  publishState();
});

function publishState() {
  const payload = JSON.stringify({ power: state.power, draw_w: state.draw_w });
  client.publish("state/fan", payload, { qos: 1, retain: true });
}

function shutdown() {
  client.end(false, {}, () => process.exit(0));
}
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
