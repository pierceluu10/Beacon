"""Whole product, no microphone, no human: synthesized speech in, real STT, real Claude, headless Chrome, spoken lines captured."""
import asyncio, re, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent)); sys.path.insert(0, str(Path(__file__).parent))
import fake_microphone as mic
import voice
from main import Config, build_agent, converse
from voice import Ears

F, WAV = Path(sys.argv[1]), Path(sys.argv[1]) / "wav"
APPLY = "http://localhost:8771/apply.html"
voice.SAY = "/usr/bin/true"
T0 = time.perf_counter(); now = lambda: time.perf_counter() - T0
spoken: list[tuple[float, str]] = []; said_at = {}
def user(name):
    audio = mic.clip(WAV / f"{name}.wav"); mic.pending.append(audio); said_at[name] = now() + len(audio) / mic.RATE
    print(f"[{now():6.1f}s] USER  {name}.wav (ends at {said_at[name]:.1f}s)")
async def until(predicate, timeout=120):
    async with asyncio.timeout(timeout):
        while not any(predicate(line) for _, line in spoken): await asyncio.sleep(0.05)
    return next(at for at, line in spoken if predicate(line))

async def run():
    config = Config(profile_dir=str(F / "e2e/profile"), resume_path=str(F / "uploads/resume.pdf"), facts_path=str(F / "e2e/facts.md"))
    async with Ears(config.stt_model, [config.agent_name], config.say_voice) as ears, build_agent(config, ears, headless=True) as agent:
        t = time.perf_counter(); await agent.warm_up(); print(f"warm-up {time.perf_counter()-t:.2f}s")
        real_say = ears.say
        async def say(text):
            spoken.append((now(), text)); print(f"[{now():6.1f}s] AGENT {text!r}"); await real_say(text)
        ears.say = say
        log = lambda text: (re.search(r'"?(LOG:[^"\n]*)', text) or ["", "?"])[1]
        await agent._call("browser_navigate", {"url": APPLY})
        loop = asyncio.create_task(converse(ears, agent, config))
        await asyncio.sleep(1)
        print("== 1. wake and command in one breath, spoken yes")
        user("command"); at = await until(lambda line: line == "On it."); print(f"   'On it.' {at - said_at['command']:.2f}s after the user stopped")
        at = await until(lambda line: line.startswith("Ready to press Submit application")); print(f"   gate question {at - said_at['command']:.1f}s after the user stopped")
        assert "SUBMITTED" not in await agent._call("browser_snapshot", {}), "submitted before the spoken yes"
        await asyncio.sleep(2.5); count = len(spoken); user("yes")
        async with asyncio.timeout(60):
            while len(spoken) == count: await asyncio.sleep(0.05)
        page = log(await agent._call("browser_snapshot", {})); print("   PAGE", page); assert "SUBMITTED:Pierce Luu:pierce@example.com:resume.pdf" in page
        print("== 2. follow-up without the wake word, history kept")
        await asyncio.sleep(1.5); count = len(spoken); user("followup")
        async with asyncio.timeout(60):
            while len(spoken) < count + 2: await asyncio.sleep(0.05)
        assert "acme" in spoken[-1][1].lower(), spoken[-1]
        await asyncio.sleep(1.5); user("thanks"); await asyncio.sleep(3)
        print("== 3. stop in the middle of a task")
        spoken.clear(); user("slow"); await until(lambda line: line == "On it."); await asyncio.sleep(6)
        user("stop"); at = await until(lambda line: line == "Stopped.", 15); print(f"   'Stopped.' {at - said_at['stop']:.2f}s after the user stopped")
        await asyncio.sleep(1.5); user("thanks"); await asyncio.sleep(3)
        print("== 4. it still works after a stop; a spoken no leaves the form alone")
        await agent._call("browser_navigate", {"url": APPLY}); spoken.clear(); user("command")
        await until(lambda line: line.startswith("Ready to press Submit application")); await asyncio.sleep(2.5); count = len(spoken); user("no")
        async with asyncio.timeout(60):
            while len(spoken) == count: await asyncio.sleep(0.05)
        page = log(await agent._call("browser_snapshot", {})); print("   PAGE", page); assert "SUBMITTED" not in page
        loop.cancel()
        print("history roles:", [m["role"] for m in agent._messages]); print("E2E PASSED")
asyncio.run(run())
