import mqtt from "mqtt";

const MQTT_URL = process.env.MQTT_URL ?? "mqtt://localhost:1883";
const AC_DRAW_W = Number(process.env.AC_DRAW_W ?? 1200);
const AC_RAMP_MS = Number(process.env.AC_RAMP_MS ?? 2000);
const STEP_MS = 250;

type Power = "on" | "off";
type FanSpeed = "low" | "med" | "high";

interface AcCmd {
  source?: "esp32" | "user";
  power: Power;
  temp_c?: number;
  fan_speed?: FanSpeed;
}

const state = {
  power: "off" as Power,
  temp_c: 25,
  fan_speed: "low" as FanSpeed,
  draw_w: 0,
};

let ramp: ReturnType<typeof setInterval> | null = null;

const client = mqtt.connect(MQTT_URL, {
  clientId: `ac-mock-${Math.random().toString(16).slice(2, 8)}`,
  reconnectPeriod: 2000,
});

client.on("connect", () => {
  console.log(`[ac-mock] connected: ${MQTT_URL}`);
  client.subscribe("cmd/ac", { qos: 1 }, (err) => {
    if (err) console.error("[ac-mock] subscribe error:", err);
  });
  publishState();
});

client.on("error", (err) => console.error("[ac-mock] mqtt error:", err));

client.on("message", (topic, payload) => {
  if (topic !== "cmd/ac") return;
  let cmd: AcCmd;
  try {
    cmd = JSON.parse(payload.toString()) as AcCmd;
  } catch (e) {
    console.warn("[ac-mock] bad json:", payload.toString());
    return;
  }
  if (cmd.power !== "on" && cmd.power !== "off") {
    console.warn("[ac-mock] missing/invalid power:", cmd);
    return;
  }
  applyCmd(cmd);
});

function applyCmd(cmd: AcCmd) {
  const prevPower = state.power;
  state.power = cmd.power;
  if (typeof cmd.temp_c === "number") state.temp_c = cmd.temp_c;
  if (cmd.fan_speed) state.fan_speed = cmd.fan_speed;

  console.log(`[ac-mock] cmd from=${cmd.source ?? "?"} power=${cmd.power} temp=${state.temp_c} fan=${state.fan_speed}`);

  if (state.power === "on" && prevPower === "off") {
    startRamp(AC_DRAW_W);
  } else if (state.power === "off") {
    stopRamp();
    state.draw_w = 0;
    publishState();
  } else {
    // already on, settings changed only — keep draw_w as is, just publish update
    publishState();
  }
}

function startRamp(target: number) {
  stopRamp();
  const steps = Math.max(1, Math.round(AC_RAMP_MS / STEP_MS));
  const increment = target / steps;
  let i = 0;
  ramp = setInterval(() => {
    i += 1;
    state.draw_w = Math.min(target, Math.round(increment * i));
    publishState();
    if (i >= steps) stopRamp();
  }, STEP_MS);
}

function stopRamp() {
  if (ramp) {
    clearInterval(ramp);
    ramp = null;
  }
}

function publishState() {
  const payload = JSON.stringify({ power: state.power, draw_w: state.draw_w });
  client.publish("state/ac", payload, { qos: 1, retain: true });
}

function shutdown() {
  stopRamp();
  client.end(false, {}, () => process.exit(0));
}
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
