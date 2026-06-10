import { Elysia, t } from "elysia";
import { inputAt, type Scenario } from "./solar";
import { Meter } from "./meter";

const MQTT_URL = process.env.MQTT_URL ?? "mqtt://localhost:1883";
const HTTP_PORT = Number(process.env.HTTP_PORT ?? 3001);
const BASE_LOAD_W = Number(process.env.BASE_LOAD_W ?? 150);
let scenario: Scenario = ((process.env.SCENARIO as Scenario) ?? "sunny");
let fixedHour: number | null =
  process.env.FIXED_HOUR != null ? Number(process.env.FIXED_HOUR) : null;

const meter = new Meter(MQTT_URL, BASE_LOAD_W);

const app = new Elysia()
  // Real contract endpoint.
  .get("/power", () => ({
    ts: Date.now(),
    input_w: round1(inputAt(new Date(), scenario, fixedHour)),
    usage_w: round1(meter.usageW()),
  }))
  // Mock-only dev endpoints. NOT part of the real solar API contract.
  .get("/scenario", () => ({ name: scenario }))
  .post(
    "/scenario",
    ({ body }) => {
      scenario = body.name;
      console.log(`[solar] scenario -> ${scenario}`);
      return { name: scenario };
    },
    {
      body: t.Object({
        name: t.Union([t.Literal("sunny"), t.Literal("cloudy"), t.Literal("night")]),
      }),
    },
  )
  // Mock-only: freeze the solar curve at a given hour (e.g. 12 = solar noon)
  // so the system is testable at night. null = follow the real clock.
  .get("/clock", () => ({ fixed_hour: fixedHour }))
  .post(
    "/clock",
    ({ body }) => {
      fixedHour = body.fixed_hour;
      console.log(`[solar] fixed_hour -> ${fixedHour ?? "real clock"}`);
      return { fixed_hour: fixedHour };
    },
    {
      body: t.Object({
        fixed_hour: t.Union([t.Number({ minimum: 0, maximum: 24 }), t.Null()]),
      }),
    },
  )
  // Debug: peek at meter inputs.
  .get("/_debug", () => ({
    scenario,
    fixed_hour: fixedHour,
    base_load_w: BASE_LOAD_W,
    appliances: meter.snapshot(),
  }))
  .listen(HTTP_PORT);

console.log(`[solar] HTTP listening on :${HTTP_PORT} (scenario=${scenario}, base=${BASE_LOAD_W}W)`);

function round1(n: number): number {
  return Math.round(n * 10) / 10;
}
