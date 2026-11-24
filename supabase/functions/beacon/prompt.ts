// Beacon's own system prompts live in the prompts table, out of the public repo. The app sends a marker block such as
// <beacon:browser> in their place, and the server swaps in the stored text before the request reaches Anthropic.
const MARKER = /^<beacon:([a-z]+)>$/;

export class UnknownPromptError extends Error {}

export async function withPrompts(system: unknown, load: (name: string) => Promise<string | null>): Promise<unknown> {
  if (!Array.isArray(system)) return system;
  return await Promise.all(system.map(async (block) => {
    const name = block?.type === "text" && typeof block.text === "string" ? block.text.match(MARKER)?.[1] : undefined;
    if (!name) return block;
    const text = await load(name);
    if (text === null) throw new UnknownPromptError(`No prompt named ${name}.`);
    return { ...block, text };
  }));
}
