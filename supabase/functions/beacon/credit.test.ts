// Run with: node --test supabase/functions/beacon/credit.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { callerId, cost, CREDIT_USD, DAILY_CEILING_USD, isDevice, refusal } from "./credit.ts";

test("a typical Opus step with a cached prompt and a screenshot costs a few cents", () => {
  const usage = { input_tokens: 1800, cache_read_input_tokens: 9000, cache_creation_input_tokens: 0, output_tokens: 150 };
  const spent = cost("claude-opus-5-5", usage);
  console.log("one step:", spent);
  assert.ok(Math.abs(spent - (1800 * 4 + 9000 * 0.2 + 150 * 20) / 1e6) < 1e-12);
});

test("a cache write is charged at the write price, not the input price", () => {
  assert.equal(cost("claude-opus-5-5", { cache_creation_input_tokens: 1_000_000 }), 5);
});

test("Haiku prompts over 100K tokens cost five times as much, so a long prompt cannot ride the cheap rate", () => {
  assert.equal(cost("claude-haiku-5-5", { input_tokens: 100_000 }), 0.01);
  assert.equal(cost("claude-haiku-5-5", { input_tokens: 100_001 }), 100_001 * 0.1 * 5 / 1e6);
});

test("missing usage fields count as zero", () => {
  assert.equal(cost("claude-haiku-5-5", {}), 0);
});

test("only random (version 4) UUIDs are device ids", () => {
  assert.ok(isDevice("3f2b8c1e-9a4d-4e6f-8b2a-1c3d5e7f9a0b"));
  for (const bad of [null, "", "sk-ant-key", "3f2b8c1e-9a4d-1e6f-8b2a-1c3d5e7f9a0b", "3F2B8C1E-9A4D-4E6F-8B2A-1C3D5E7F9A0B"]) {
    assert.equal(isDevice(bad), false, String(bad));
  }
});

test("a device is refused once it reaches its credit, and everyone once the day's ceiling is reached", () => {
  assert.equal(refusal(CREDIT_USD - 0.01, 0), null);
  assert.equal(refusal(CREDIT_USD, 0)?.status, 402);
  assert.equal(refusal(0, DAILY_CEILING_USD)?.status, 503);
});

test("the device id is read from x-api-key or, as the Anthropic SDK sends it, a Bearer token", () => {
  const id = "3f2b8c1e-9a4d-4e6f-8b2a-1c3d5e7f9a0b";
  assert.equal(callerId(new Headers({ "x-api-key": id })), id);
  assert.equal(callerId(new Headers({ authorization: `Bearer ${id}` })), id);
  assert.equal(callerId(new Headers({ authorization: `Basic ${id}` })), null);
  assert.equal(callerId(new Headers()), null);
});
