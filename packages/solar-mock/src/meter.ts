import mqtt, { type MqttClient } from "mqtt";

type ApplianceState = { power: "on" | "off"; draw_w: number };

export class Meter {
  private client: MqttClient;
  private appliances: Record<string, ApplianceState> = {};
  private baseLoad: number;

  constructor(mqttUrl: string, baseLoadW: number) {
    this.baseLoad = baseLoadW;
    this.client = mqtt.connect(mqttUrl, {
      clientId: `solar-mock-meter-${Math.random().toString(16).slice(2, 8)}`,
      reconnectPeriod: 2000,
    });

    this.client.on("connect", () => {
      console.log(`[meter] mqtt connected: ${mqttUrl}`);
      // QoS 1 per contract. Retained messages flow automatically on subscribe.
      this.client.subscribe(["state/ac", "state/fan"], { qos: 1 }, (err) => {
        if (err) console.error("[meter] subscribe error:", err);
      });
    });

    this.client.on("error", (err) => console.error("[meter] mqtt error:", err));

    this.client.on("message", (topic, payload) => {
      try {
        const msg = JSON.parse(payload.toString()) as ApplianceState;
        if (!msg || typeof msg.draw_w !== "number" || (msg.power !== "on" && msg.power !== "off")) {
          console.warn(`[meter] bad payload on ${topic}:`, payload.toString());
          return;
        }
        this.appliances[topic] = msg;
      } catch (e) {
        console.warn(`[meter] parse error on ${topic}:`, e);
      }
    });
  }

  // Sum metered consumption. Off appliances contribute 0.
  usageW(): number {
    let total = this.baseLoad;
    for (const s of Object.values(this.appliances)) {
      if (s.power === "on") total += s.draw_w;
    }
    return total;
  }

  snapshot(): Record<string, ApplianceState> {
    return { ...this.appliances };
  }
}
