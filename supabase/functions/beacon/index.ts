// Beacon's hosted keys: forwards Claude and ElevenLabs requests with the owner's keys, swaps in Beacon's stored prompts and
// stops each Mac at $1 of Claude.
// The device id arrives as the Anthropic SDK's api_key, or as x-api-key from the voice.
import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "npm:@supabase/supabase-js@2";
import {
  callerId,
  cost,
  CREDIT_USD,
  isDevice,
  MAX_OUTPUT_TOKENS,
  MAX_SPEECH_CHARACTERS,
  NEW_DEVICES_PER_IP_PER_DAY,
  PRICES,
  refusal,
  streamedUsage,
} from "./credit.ts";
import { UnknownPromptError, withPrompts } from "./prompt.ts";

const ANTHROPIC_MESSAGES = "https://api.anthropic.com/v1/messages";
const ELEVENLABS_SPEECH = "https://api.elevenlabs.io/v1/text-to-speech/";
const DAY_MILLISECONDS = 86_400_000;
const FORWARDED_ANTHROPIC_HEADERS = ["anthropic-version", "anthropic-beta"];
const SPEECH_PATH = /\/v1\/text-to-speech\/([A-Za-z0-9]+)\/stream$/;

const db = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);

type Device = { id: string; spent_usd: number; credit_requested_at: string | null };

function failure(status: number, type: string, message: string): Response {
  return Response.json({ type: "error", error: { type, message } }, { status });
}

// The calling device, registering it on first sight, or the Response that refuses it.
async function device(request: Request): Promise<Device | Response> {
  const id = callerId(request.headers);
  if (!isDevice(id)) return failure(401, "authentication_error", "Missing or malformed Beacon device id.");
  const { data: known, error } = await db.from("devices").select("id, spent_usd, credit_requested_at").eq("id", id)
    .maybeSingle();
  if (error) throw error;
  if (known) return known;
  const ip = request.headers.get("x-forwarded-for")?.split(",")[0].trim() ?? "unknown";
  const since = new Date(Date.now() - DAY_MILLISECONDS).toISOString();
  const { count, error: counting } = await db.from("devices").select("id", { count: "exact", head: true }).eq("ip", ip)
    .gte("created_at", since);
  if (counting) throw counting;
  if ((count ?? 0) >= NEW_DEVICES_PER_IP_PER_DAY) {
    return failure(429, "rate_limit_error", "Too many new Beacon installs from this network today.");
  }
  const { data: created, error: creating } = await db.from("devices").insert({ id, ip })
    .select("id, spent_usd, credit_requested_at").single();
  if (creating) throw creating;
  return created;
}

const prompts = new Map<string, string | null>(); // per isolate: a prompt edit reaches new isolates within minutes

async function storedPrompt(name: string): Promise<string | null> {
  if (!prompts.has(name)) {
    const { data, error } = await db.from("prompts").select("text").eq("name", name).maybeSingle();
    if (error) throw error;
    prompts.set(name, data?.text ?? null);
  }
  return prompts.get(name)!;
}

async function spentToday(): Promise<number> {
  const today = new Date().toISOString().slice(0, 10);
  const { data, error } = await db.from("daily_spend").select("usd").eq("day", today).maybeSingle();
  if (error) throw error;
  return Number(data?.usd ?? 0);
}

function refused(caller: Device, today: number): Response | null {
  const reason = refusal(Number(caller.spent_usd), today);
  return reason && failure(reason.status, reason.type, reason.message);
}

// Counting runs after the response is on its way (EdgeRuntime.waitUntil), so the user never waits on the database.
async function count(rpc: string, args: Record<string, unknown>): Promise<void> {
  const { error } = await db.rpc(rpc, args);
  if (error) console.error(`${rpc} failed`, error);
}

async function messages(request: Request, caller: Device): Promise<Response> {
  const body = await request.json();
  if (!(body.model in PRICES)) return failure(400, "invalid_request_error", `Model ${body.model} is not offered.`);
  body.max_tokens = Math.min(body.max_tokens ?? MAX_OUTPUT_TOKENS, MAX_OUTPUT_TOKENS);
  try {
    body.system = await withPrompts(body.system, storedPrompt);
  } catch (error) {
    if (error instanceof UnknownPromptError) return failure(400, "invalid_request_error", error.message);
    throw error;
  }
  const headers = new Headers({ "content-type": "application/json", "x-api-key": Deno.env.get("ANTHROPIC_API_KEY")! });
  for (const name of FORWARDED_ANTHROPIC_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const answer = await fetch(ANTHROPIC_MESSAGES, { method: "POST", headers, body: JSON.stringify(body) });
  if (body.stream && answer.ok) {
    const [toMac, toMeter] = answer.body!.tee(); // the meter reads to the end even if the Mac hangs up
    const spent = async () => count("add_spend", { device: caller.id, usd: cost(body.model, await streamedUsage(toMeter)) });
    EdgeRuntime.waitUntil(spent());
    return new Response(toMac, { status: answer.status, headers: { "content-type": "text/event-stream" } });
  }
  const text = await answer.text();
  const usage = answer.ok ? JSON.parse(text).usage : null;
  if (usage) EdgeRuntime.waitUntil(count("add_spend", { device: caller.id, usd: cost(body.model, usage) }));
  return new Response(text, { status: answer.status, headers: { "content-type": "application/json" } });
}

async function speech(request: Request, caller: Device, voice: string): Promise<Response> {
  const body = await request.json();
  if (typeof body.text !== "string" || body.text.length > MAX_SPEECH_CHARACTERS) {
    return failure(400, "invalid_request_error", `Speech text must be at most ${MAX_SPEECH_CHARACTERS} characters.`);
  }
  const target = `${ELEVENLABS_SPEECH}${voice}/stream${new URL(request.url).search}`;
  const answer = await fetch(target, {
    method: "POST",
    headers: { "content-type": "application/json", "xi-api-key": Deno.env.get("ELEVENLABS_API_KEY")! },
    body: JSON.stringify(body),
  });
  if (answer.ok) EdgeRuntime.waitUntil(count("add_speech", { device: caller.id, characters: body.text.length }));
  return new Response(answer.body, { status: answer.status, headers: { "content-type": "application/octet-stream" } });
}

// One request per device, once its credit is spent. The owner reviews them in the devices table and grants another dollar
// with: update devices set spent_usd = 0, credit_requested_at = null where id = '...';
async function creditRequest(caller: Device): Promise<Response> {
  if (Number(caller.spent_usd) < CREDIT_USD) return failure(400, "invalid_request_error", "This Mac still has credit.");
  if (caller.credit_requested_at) return failure(409, "already_requested", "More credit was already requested.");
  const { error } = await db.from("devices").update({ credit_requested_at: new Date().toISOString() }).eq("id", caller.id);
  if (error) throw error;
  return Response.json({ requested: true });
}

Deno.serve(async (request) => {
  if (request.method !== "POST") return failure(405, "invalid_request_error", "Only POST is offered.");
  const path = new URL(request.url).pathname;
  const [caller, today] = await Promise.all([device(request), spentToday()]);
  if (caller instanceof Response) return caller;
  if (path.endsWith("/credit-request")) return creditRequest(caller);
  const blocked = refused(caller, today);
  if (blocked) return blocked;
  if (path.endsWith("/v1/messages")) return messages(request, caller);
  const voice = path.match(SPEECH_PATH)?.[1];
  if (voice) return speech(request, caller, voice);
  return failure(404, "not_found_error", `No route for ${path}.`);
});
