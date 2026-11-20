"""No microphone, no human: synthesized speech through a fake input device into the real Ears (Moonshine VAD + STT)."""
import asyncio, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent)); sys.path.insert(0, str(Path(__file__).parent))
import fake_microphone as mic
import voice, main
from voice import Ears, MicrophoneSilentError, check_microphone, is_dismissal, is_stop, parse_yes_no, wake_command

WAV = Path(sys.argv[1]); NAMES = ("Jarvis",)
voice.SAY = "/usr/bin/true"; voice.ECHO_TAIL_SECONDS = 0.05
say = lambda name, seconds=None: mic.pending.append(mic.clip(WAV / f"{name}.wav", seconds))
checks: dict[str, bool] = {}
def check(name, ok, detail=""):
    checks[name] = bool(ok); print(f"{'ok  ' if ok else 'FAIL'} {name}  {detail}")

async def main_():
    async with Ears("MEDIUM_STREAMING", ["Jarvis"], "Samantha") as ears:
        async def heard(name, timeout=10.0):
            say(name); return await ears.hear(timeout)
        text = await heard("wake"); check("bare wake", wake_command(text, NAMES) == "", repr(text))
        text = await heard("wakecmd"); check("wake carries the command", "youtube" in (wake_command(text, NAMES) or "").lower(), repr(text))
        text = await heard("travis"); check("no false wake on Travis", wake_command(text, NAMES) is None, repr(text))
        text = await heard("yes"); check("yes", parse_yes_no(text) is True, repr(text))
        text = await heard("no"); check("no", parse_yes_no(text) is False, repr(text))
        text = await heard("nostop"); check("no stop is a stop and a no", is_stop(text) and parse_yes_no(text) is False, repr(text))
        text = await heard("stop"); check("stop", is_stop(text), repr(text))
        text = await heard("thanks"); check("thanks dismisses", is_dismissal(text), repr(text))
        check("a command is neither stop nor dismissal", not is_stop("Cancel my order on Amazon") and not is_dismissal("No, open the second one"))
        check("a long sentence with yes is not approval", parse_yes_no("yeah that's what she said about it yesterday") is None)
        # a pause mid-sentence splits the transcript; hear_whole joins it
        say("pause_700"); first = await ears.hear(10); whole = await main.hear_whole(ears, wake_command(first, NAMES))
        check("pause mid-command is joined", "youtube" in whole.lower() and "search" in whole.lower(), f"{first!r} -> {whole!r}")
        t = time.perf_counter(); none = await main.hear_whole(ears, "open the first one"); dt = time.perf_counter() - t
        check("no continuation costs only the grace", none == "open the first one" and dt < main.CONTINUATION_SECONDS + 0.3, f"{dt:.2f}s")
        # timeouts
        t = time.perf_counter(); text = await ears.hear(1.0); check("silence times out", text is None and time.perf_counter() - t < 1.3, f"{time.perf_counter()-t:.2f}s")
        say("wakecmd"); t = time.perf_counter(); text = await ears.hear(1.5)
        check("speech begun before the deadline is returned whole", text and "search" in text.lower(), f"{text!r} after {time.perf_counter()-t:.2f}s")
        # the newest listener wins
        watcher = asyncio.create_task(ears.hear()); await asyncio.sleep(0); question = asyncio.create_task(ears.hear(8)); await asyncio.sleep(0)
        say("yes"); answer = await question; check("question outranks the watcher", parse_yes_no(answer) and not watcher.done(), repr(answer))
        say("stop"); check("watcher hears the next line", is_stop(await watcher))
        # speech in progress when the agent starts to speak goes to the watcher, never into the answer
        watcher = asyncio.create_task(ears.hear()); await asyncio.sleep(0)
        say("wakecmd", 1.6); await asyncio.sleep(1.7)
        t = time.perf_counter(); await ears.say("Ready to press Submit?"); spoke = time.perf_counter() - t
        say("yes"); answer = await ears.hear(8)
        check("half a sentence before a question is flushed to the watcher", watcher.done() and parse_yes_no(answer) is True, f"watcher={watcher.result() if watcher.done() else None!r} answer={answer!r} say took {spoke:.2f}s")
        # confirm(): yes, then stop
        async def answer_later(name, after):
            await asyncio.sleep(after); say(name)
        task = asyncio.create_task(answer_later("yes", 0.3)); check("confirm yes", await main.confirm(ears, "Ready?") is True); await task
        task = asyncio.create_task(answer_later("stop", 0.3))
        try:
            await main.confirm(ears, "Ready?"); check("stop during confirm cancels the task", False)
        except asyncio.CancelledError:
            check("stop during confirm cancels the task", True)
        await task
        main.CONFIRM_SECONDS = 2; t = time.perf_counter(); check("silence at a confirmation is no", await main.confirm(ears, "Ready?") is False, f"{time.perf_counter()-t:.2f}s")
    mic.silent[0] = True; voice.MICROPHONE_CHECK_ATTEMPTS = 2
    try:
        check_microphone(); check("silent microphone raises", False)
    except MicrophoneSilentError:
        check("silent microphone raises", True)
    print("FAILURES:", [name for name, ok in checks.items() if not ok] or "none")
asyncio.run(main_())
