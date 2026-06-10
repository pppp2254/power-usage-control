const wsUrl = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
const $ = (id) => document.getElementById(id);
const conn = $("conn");

let ws;
let backoff = 500;
let currentMode = null;

function connect() {
  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    backoff = 500;
    conn.textContent = "connected";
    conn.classList.remove("offline");
    conn.classList.add("online");
  };

  ws.onclose = () => {
    conn.textContent = "disconnected";
    conn.classList.remove("online");
    conn.classList.add("offline");
    setTimeout(connect, backoff);
    backoff = Math.min(backoff * 2, 10000);
  };

  ws.onerror = (e) => console.error("ws error", e);

  ws.onmessage = (e) => {
    let env;
    try { env = JSON.parse(e.data); } catch { return; }
    if (env.type === "msg" && env.topic === "telemetry/esp32") {
      render(env.payload);
    } else if (env.type === "error") {
      console.error("server:", env.error);
    }
  };
}

function publish(topic, payload) {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    console.warn("ws not open, drop publish");
    return;
  }
  ws.send(JSON.stringify({ type: "publish", topic, payload }));
}

function render(t) {
  if (!t || typeof t !== "object") return;
  $("input_w").textContent = fmt(t.input_w);
  $("usage_w").textContent = fmt(t.usage_w);
  $("available_w").textContent = fmt(t.available_w);
  $("ts").textContent = t.ts ? new Date(t.ts).toLocaleTimeString() : "—";
  $("mode_label").textContent = t.mode ?? "—";
  currentMode = t.mode ?? null;
  setActiveMode(t.mode);
  renderAppliance("ac_state", t.ac);
  renderAppliance("fan_state", t.fan);
  $("override_until").textContent = t.override_until
    ? new Date(t.override_until).toLocaleTimeString()
    : "—";
}

function renderAppliance(rootId, a) {
  const root = document.getElementById(rootId);
  if (!root || !a) return;
  for (const k of ["desired", "commanded", "observed"]) {
    const el = root.querySelector(`[data-k="${k}"]`);
    if (el) el.textContent = a[k] ?? "—";
  }
}

function setActiveMode(mode) {
  $("mode_auto").classList.toggle("active", mode === "auto");
  $("mode_manual").classList.toggle("active", mode === "manual");
}

function fmt(n) {
  return typeof n === "number" ? `${n.toFixed(1)} W` : "—";
}

// Mode toggle
$("mode_auto").onclick = () =>
  publish("cmd/esp32", { mode: "auto", ac: null, fan: null });
$("mode_manual").onclick = () =>
  publish("cmd/esp32", { mode: "manual", ac: null, fan: null });

// Manual on/off via ESP32 (cmd/esp32)
for (const btn of document.querySelectorAll("[data-cmd]")) {
  btn.onclick = () => {
    const key = btn.dataset.cmd;       // "ac" | "fan"
    const val = btn.dataset.val;       // "on" | "off"
    if (currentMode !== "manual") {
      alert("Switch to manual mode first (ESP32 ignores ac/fan in auto).");
      return;
    }
    const body = { mode: null, ac: null, fan: null };
    body[key] = val;
    publish("cmd/esp32", body);
  };
}

// Direct "remote" - bypass ESP32, publish to cmd/ac or cmd/fan with source=user.
for (const btn of document.querySelectorAll("[data-direct]")) {
  btn.onclick = () => {
    const which = btn.dataset.direct;       // "ac" | "fan"
    const power = btn.dataset.power;        // "on" | "off"
    if (which === "ac") {
      publish("cmd/ac", { source: "user", power, temp_c: 25, fan_speed: "low" });
    } else {
      publish("cmd/fan", { source: "user", power, speed: "med" });
    }
  };
}

connect();
