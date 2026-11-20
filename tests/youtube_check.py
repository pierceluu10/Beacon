"""Live site, no voice: the agent loop on YouTube (headless), counting questions, steps, tokens and time."""
import asyncio, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from agent import Agent

F = Path(sys.argv[1]); asked = []
async def confirm(question): asked.append(question); print("   GATE ASKED:", question); return False
async def ask(question): print("   ASK_USER:", question); return "continue"
PROMPT = (Path(__file__).parent.parent / "prompt.txt").read_text().format(agent_name="Jarvis", user_name="Pierce", upload_dir=F / "uploads",
    resume_name="resume.pdf", uploads="resume.pdf", facts="Name: Pierce Luu")
async def main():
    async with Agent("claude-opus-5-5", F / "e2e/profile-yt", F / "uploads", PROMPT, frozenset(), confirm, ask, headless=True) as agent:
        await agent.warm_up()
        create = agent._anthropic.messages.create; calls = []
        async def logged(**kwargs):
            t = time.perf_counter(); reply = await create(**kwargs); u = reply.usage
            calls.append((time.perf_counter() - t, u.input_tokens, u.cache_read_input_tokens, u.cache_creation_input_tokens, u.output_tokens))
            print(f"   model call {calls[-1][0]:.2f}s in={u.input_tokens} cache_read={u.cache_read_input_tokens} cache_write={u.cache_creation_input_tokens} out={u.output_tokens} stop={reply.stop_reason} tools={[b.name for b in reply.content if b.type == 'tool_use']}")
            return reply
        agent._anthropic.messages.create = logged
        for command in sys.argv[2:]:
            asked.clear(); calls.clear(); t = time.perf_counter()
            print(f"COMMAND: {command}")
            answer = await agent.run(command)
            cost = sum(i * 4 + r * 0.2 + w * 5 + o * 20 for _, i, r, w, o in calls) / 1e6
            print(f"ANSWER ({time.perf_counter()-t:.1f}s, {len(calls)} model calls, ${cost:.3f}, questions asked: {len(asked)}): {answer}")
            print("   media playing:", await agent.media_playing(), "| snapshot chars now:", len(await agent._call('browser_snapshot', {})))
asyncio.run(main())
