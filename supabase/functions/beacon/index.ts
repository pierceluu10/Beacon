// Beacon's hosted keys: forwards Claude and ElevenLabs requests with the owner's keys and stops each Mac at $1 of Claude.
// The device id arrives as x-api-key, which is where the Anthropic SDK puts its api_key.
import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "npm:@supabase/supabase-js@2";
import {
  alertsDue,
  cost,
  CREDIT_USD,
  isDevice,
  MAX_OUTPUT_TOKENS,
  MAX_SPEECH_CHARACTERS,
  NEW_DEVICES_PER_IP_PER_DAY,
  OWNER_EMAIL,
  PRICES,
  refusal,
  type Today,
} from "./credit.ts";

const ANTHROPIC_MESSAGES = "https://api.anthropic.com/v1/messages";
const ELEVENLABS_SPEECH = "https://api.elevenlabs.io/v1/text-to-speech/";
const RESEND_EMAILS = "https://api.resend.com/emails";
const EMAIL_SENDER = "Beacon <onboarding@resend.dev>"; // Resend's shared sender; works before a domain is verified
const DAY_MILLISECONDS = 86_400_000;
const FORWARDED_ANTHROPIC_HEADERS = ["anthropic-version", "anthropic-beta"];
const SPEECH_PATH = /\/v1\/text-to-speech\/([A-Za-z0-9]+)\/stream$/;

const db = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);
const RESEND_API_KEY = Deno.env.get("RESEND_API_KEY");

type Device = { id: string; spent_usd: number; credit_requested_at: string | null };

function failure(status: number, type: string, message: string): Response {
  return Response.json({ type: "error", error: { type, message } }, { status });
}

// The calling device, registering it on first sight, or the Response that refuses it.
async function device(request: Request): Promise<Device | Response> {
  const id = request.headers.get("x-api-key");
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

async function spentToday(): Promise<number> {
  const today = new Date().toISOString().slice(0, 10);
  const { data, error } = await db.from("daily_spend").select("usd").eq("day", today).maybeSingle();
  if (error) throw error;
  return Number(data?.usd ?? 0);
}

async function refused(caller: Device): Promise<Response | null> {
  const reason = refusal(Number(caller.spent_usd), await spentToday());
  return reason && failure(reason.status, reason.type, reason.message);
}

function email(subject: string, text: string): Promise<Response> {
  return fetch(RESEND_EMAILS, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${RESEND_API_KEY}` },
    body: JSON.stringify({ from: EMAIL_SENDER, to: [OWNER_EMAIL], subject, text }),
  });
}

// Emails each alert today's totals have newly crossed. Runs after the response, so a slow email never delays a request.
async function alert(today: Today): Promise<void> {
  if (!RESEND_API_KEY) return;
  for (const due of alertsDue(today)) {
    const { data: claimed, error } = await db.rpc("claim_alert", { alert: due.key });
    if (error) throw error;
    if (!claimed) continue;
    const sent = await email(due.subject, due.text);
    if (!sent.ok) console.error(`Alert ${due.key} was claimed but the email service answered ${sent.status}.`);
  }
}

async function record(rpc: "add_spend" | "add_speech", args: Record<string, unknown>): Promise<void> {
  const { data: today, error } = await db.rpc(rpc, args);
  if (error) throw error;
  EdgeRuntime.waitUntil(alert(today));
}

async function messages(request: Request, caller: Device): Promise<Response> {
  const body = await request.json();
  if (!(body.model in PRICES)) return failure(400, "invalid_request_error", `Model ${body.model} is not offered.`);
  if (body.stream) return failure(400, "invalid_request_error", "Streaming is not offered.");
  body.max_tokens = Math.min(body.max_tokens ?? MAX_OUTPUT_TOKENS, MAX_OUTPUT_TOKENS);
  const headers = new Headers({ "content-type": "application/json", "x-api-key": Deno.env.get("ANTHROPIC_API_KEY")! });
  for (const name of FORWARDED_ANTHROPIC_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const answer = await fetch(ANTHROPIC_MESSAGES, { method: "POST", headers, body: JSON.stringify(body) });
  const text = await answer.text();
  if (answer.ok) {
    await record("add_spend", { device: caller.id, usd: cost(body.model, JSON.parse(text).usage) });
  }
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
  if (answer.ok) await record("add_speech", { device: caller.id, characters: body.text.length });
  return new Response(answer.body, { status: answer.status, headers: { "content-type": "application/octet-stream" } });
}

// One email per device to the owner, once its credit is spent.
async function creditRequest(caller: Device): Promise<Response> {
  if (Number(caller.spent_usd) < CREDIT_USD) return failure(400, "invalid_request_error", "This Mac still has credit.");
  if (caller.credit_requested_at) return failure(409, "already_requested", "More credit was already requested.");
  if (!RESEND_API_KEY) return failure(503, "email_unavailable", "Credit requests are not set up yet.");
  const sent = await email(
    "A Beacon user wants more credit",
    `Device ${caller.id} has spent $${Number(caller.spent_usd).toFixed(2)} and asked for more credit.\n\n` +
      `To give it another dollar: update devices set spent_usd = 0, credit_requested_at = null where id = '${caller.id}';`,
  );
  if (!sent.ok) return failure(502, "email_failed", `The email service answered ${sent.status}.`);
  const { error } = await db.from("devices").update({ credit_requested_at: new Date().toISOString() }).eq("id", caller.id);
  if (error) throw error;
  return Response.json({ sent: true });
}

Deno.serve(async (request) => {
  if (request.method !== "POST") return failure(405, "invalid_request_error", "Only POST is offered.");
  const path = new URL(request.url).pathname;
  const caller = await device(request);
  if (caller instanceof Response) return caller;
  if (path.endsWith("/credit-request")) return creditRequest(caller);
  const blocked = await refused(caller);
  if (blocked) return blocked;
  if (path.endsWith("/v1/messages")) return messages(request, caller);
  const voice = path.match(SPEECH_PATH)?.[1];
  if (voice) return speech(request, caller, voice);
  return failure(404, "not_found_error", `No route for ${path}.`);
});
