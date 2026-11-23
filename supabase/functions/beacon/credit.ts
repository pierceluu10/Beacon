// The rules for Beacon's hosted credit, kept apart from the server so they can be tested without Deno or a network.
export const CREDIT_USD = 1;
export const DAILY_CEILING_USD = 50; // all devices together: a backstop if someone mints device ids in a loop
export const NEW_DEVICES_PER_IP_PER_DAY = 3;
export const MAX_OUTPUT_TOKENS = 4096; // Beacon's own largest request; also bounds how far one request can overshoot $1
export const MAX_SPEECH_CHARACTERS = 1000;

const MILLION = 1_000_000;
const HAIKU_LONG_PROMPT_TOKENS = 100_000; // Haiku 5.5 costs five times as much above this
const HAIKU_LONG_PROMPT_FACTOR = 5;

type Price = { input: number; output: number; cacheRead: number; cacheWrite: number }; // US$ per million tokens
export const PRICES: Record<string, Price> = {
  "claude-opus-5-5": { input: 4, output: 20, cacheRead: 0.2, cacheWrite: 5 },
  "claude-haiku-5-5": { input: 0.1, output: 0.5, cacheRead: 0.01, cacheWrite: 0.125 },
};

export type Usage = {
  input_tokens?: number;
  output_tokens?: number;
  cache_read_input_tokens?: number;
  cache_creation_input_tokens?: number;
};

export function cost(model: string, usage: Usage): number {
  const price = PRICES[model];
  const input = usage.input_tokens ?? 0, read = usage.cache_read_input_tokens ?? 0;
  const written = usage.cache_creation_input_tokens ?? 0, output = usage.output_tokens ?? 0;
  const long = model === "claude-haiku-5-5" && input + read + written > HAIKU_LONG_PROMPT_TOKENS;
  const factor = long ? HAIKU_LONG_PROMPT_FACTOR : 1;
  return factor * (input * price.input + read * price.cacheRead + written * price.cacheWrite + output * price.output) / MILLION;
}

const DEVICE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

export function isDevice(id: string | null): id is string {
  return id !== null && DEVICE.test(id);
}

// Why a request is refused before it costs anything, or null to let it through.
export function refusal(spent: number, spentToday: number): { status: number; type: string; message: string } | null {
  if (spent >= CREDIT_USD) {
    return { status: 402, type: "credit_exhausted", message: `This Mac has used its $${CREDIT_USD} of free Beacon credit.` };
  }
  if (spentToday >= DAILY_CEILING_USD) {
    return { status: 503, type: "paused", message: "Beacon's free credit is paused for today. Try again tomorrow." };
  }
  return null;
}
