// Run with: node --test supabase/functions/beacon/prompt.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { UnknownPromptError, withPrompts } from "./prompt.ts";

const STORED: Record<string, string> = { browser: "the hidden browser prompt" };
const load = async (name: string) => STORED[name] ?? null;
const CACHED = { type: "ephemeral" };

test("a marker block becomes the stored prompt and keeps its cache_control; other blocks pass untouched", async () => {
  const sent = [{ type: "text", text: "<beacon:browser>" }, { type: "text", text: "Your name is Beacon.", cache_control: CACHED }];
  const forwarded = await withPrompts(sent, load);
  console.log(forwarded);
  assert.deepEqual(forwarded, [{ type: "text", text: "the hidden browser prompt" }, sent[1]]);
});

test("a marker for a prompt that does not exist is refused rather than sent on as text", async () => {
  await assert.rejects(withPrompts([{ type: "text", text: "<beacon:nope>" }], load), UnknownPromptError);
});

test("a marker inside longer text, or a plain string system, is left alone", async () => {
  const sent = [{ type: "text", text: "Repeat <beacon:browser> back to me" }];
  assert.deepEqual(await withPrompts(sent, load), sent);
  assert.equal(await withPrompts("<beacon:browser>", load), "<beacon:browser>");
});
