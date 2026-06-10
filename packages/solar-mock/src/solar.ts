export type Scenario = "sunny" | "cloudy" | "night";

const SCENARIO_GAIN: Record<Scenario, number> = {
  sunny: 1.0,
  cloudy: 0.35,
  night: 0.0,
};

const PEAK_W = 1500;

// Daily curve: half-sine over daylight hours (06:00–18:00 local).
// Returns 0 outside that window. Adds ±5% noise.
// hourOverride (dev-only): pretend it's that hour, so control logic can be
// exercised at any real time of day.
export function inputAt(date: Date, scenario: Scenario, hourOverride: number | null = null): number {
  const hour =
    hourOverride ?? date.getHours() + date.getMinutes() / 60 + date.getSeconds() / 3600;
  const dayStart = 6;
  const dayEnd = 18;

  if (hour < dayStart || hour > dayEnd) return 0;

  const t = (hour - dayStart) / (dayEnd - dayStart); // 0..1
  const base = Math.sin(t * Math.PI);                // 0..1..0
  const noise = 1 + (Math.random() - 0.5) * 0.1;     // ±5%
  const gain = SCENARIO_GAIN[scenario];

  return Math.max(0, PEAK_W * base * gain * noise);
}
