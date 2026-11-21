"""Beacon: say its name or hold the push-to-talk key, speak, and watch it use your computer."""
import asyncio
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from functools import partial
from pathlib import Path

import anthropic
import sounddevice
from mcp import MCPError

from agent import Agent, BrowserClosedError, BrowserStartError
from mac import MacAgent, MacPermissionError
from memory import Memory
from mouth import Mouth
from side import CHANGE, STOP, Side
from voice import Ears, Heard, MicrophoneSilentError, is_dismissal, is_stop, parse_yes_no, wake_command

HOME = Path("~/.beacon").expanduser()
CONFIG_PATH = HOME / "config.toml"
MEMORY_NOTES = HOME / "memory.md"
MEMORY_FOLDER = HOME / "memory"  # also the only folder the browser modes may upload from
CONTINUATION_SECONDS = 0.8  # a pause mid-sentence puts 0.47 to 0.52 s between its two transcripts; 0.6 missed some under load
FOLLOW_UP_SECONDS = 8
CONFIRM_SECONDS = 8
CONFIRM_ATTEMPTS = 2
ASK_SECONDS = 180
FORGET_AFTER_SECONDS = 600
STOPPED = "Stopped."
CONFIRM_RETRY = "Sorry, yes or no?"
CRASHED = "Something broke and I have to quit. The details are in the terminal."
NO_MICROPHONE = "I can't hear the microphone. Allow it for this terminal in System Settings, then start me again."
SPOKEN_ERRORS: dict[type[Exception], str] = {  # most specific first
    anthropic.AuthenticationError: "The Anthropic API key was rejected.",
    anthropic.RateLimitError: "Anthropic is busy. Try again in a minute.",
    anthropic.OverloadedError: "Anthropic is busy. Try again in a minute.",
    anthropic.APIConnectionError: "I can't reach Anthropic.",
    anthropic.APIStatusError: "Anthropic returned an error, so I stopped.",
    BrowserClosedError: "The browser window was closed, so I stopped.",
    BrowserStartError: "I couldn't open Chrome. The reason is in the terminal.",
    MCPError: "The browser stopped responding, so I stopped.",
    MacPermissionError: "I need permission to see and control the screen. The steps are in the terminal.",
    MicrophoneSilentError: NO_MICROPHONE,
    sounddevice.PortAudioError: NO_MICROPHONE,
}
SYSTEM_PROMPT = Path(__file__).with_name("prompt.txt").read_text()
MAC_PROMPT = Path(__file__).with_name("mac_prompt.txt").read_text()
WINDOW, CHROME, MAC = "window", "chrome", "mac"  # what it works in: its own Chrome window, your Chrome, or the whole Mac


class ConfigError(ValueError):
    """config.toml has a key this program does not know, or puts the Chrome profile where it could be uploaded."""


@dataclass(frozen=True)
class Config:
    agent_name: str = "Beacon"
    wake_aliases: list[str] = field(default_factory=list)
    model: str = "claude-opus-5-5"
    quick_model: str = "claude-haiku-5-5"  # the greeting and replies while a task runs
    profile_dir: str = str(HOME / "chrome-profile")
    blocked_hosts: list[str] = field(default_factory=list)
    stt_model: str = "MEDIUM_STREAMING"
    voice: str = "JBFqnCBsd6RMkjVDRZzb"  # an ElevenLabs voice id; used when ELEVENLABS_API_KEY is set
    voice_model: str = "eleven_v4_turbo"
    say_voice: str = "Samantha"  # the macOS voice without an ElevenLabs key
    extension_token: str = ""  # for CHROME: the Playwright Extension's token

    @property
    def names(self) -> tuple[str, ...]:
        return (self.agent_name, *self.wake_aliases)


def load_config(path: Path = CONFIG_PATH) -> Config:
    values = tomllib.loads(path.read_text()) if path.exists() else {}
    unknown = set(values) - {known.name for known in fields(Config)}
    if unknown:
        raise ConfigError(f"Unknown keys in {path}: {sorted(unknown)}")
    return Config(**values)


def spoken_error(error: Exception) -> str:
    return next(line for kind, line in SPOKEN_ERRORS.items() if isinstance(error, kind))


async def hear_whole(ears: Ears, first: str | None) -> str | None:
    """first plus whatever follows right away, so a pause in the middle of a sentence does not cut it in two."""
    while first is not None and (more := await ears.hear(CONTINUATION_SECONDS)):
        if is_stop(more):
            return more  # a change of mind, not the rest of the sentence
        first = f"{first} {more}".strip()
    return first


def addressed(heard: Heard, names: tuple[str, ...]) -> str | None:
    """The command in an utterance meant for Beacon ("" for its name alone), or None when it was not addressed. Holding the
    push-to-talk key addresses it without the name."""
    command = wake_command(heard, names)
    return str(heard) if command is None and heard.pushed else command


async def answer_to(ears: Ears, question: str, seconds: float, names: tuple[str, ...]) -> str | None:
    await ears.say(question)
    answer = await ears.hear(seconds)
    if answer and is_stop(answer, names):
        raise asyncio.CancelledError  # "stop" as an answer ends the whole task, not just this step
    return answer


async def confirm(ears: Ears, question: str, names: tuple[str, ...]) -> bool:
    """Ask out loud and return True only on a clear spoken yes; silence and no both mean no."""
    for attempt in range(CONFIRM_ATTEMPTS):
        answer = await answer_to(ears, CONFIRM_RETRY if attempt else question, CONFIRM_SECONDS, names)
        verdict = parse_yes_no(answer) if answer else False
        if verdict is not None:
            return verdict
    return False


async def ask(ears: Ears, question: str, names: tuple[str, ...]) -> str | None:
    return await hear_whole(ears, await answer_to(ears, question, ASK_SECONDS, names))


async def command_after_name(ears: Ears) -> str | None:
    """After the name alone: a chime, then the command, with the microphone open for it."""
    ears.chime()
    return await hear_whole(ears, await ears.hear(FOLLOW_UP_SECONDS))


async def listen_while_working(ears: Ears, agent: Agent | MacAgent, side: Side, stop: Callable[[str], None],
                               names: tuple[str, ...]) -> None:
    """Hear the user during a task: "stop" stops it at once; anything else said to Beacon gets a quick spoken reply about
    the task, and changes or stops it when that is what they want."""
    while True:
        heard = await ears.hear()
        if is_stop(heard, names):  # with or without the name, anywhere: there is no time to say it first
            stop(STOPPED)
            continue
        said = addressed(heard, names)
        if said is None or not said and not (said := await command_after_name(ears)):
            continue
        if is_stop(said, names):
            stop(STOPPED)
            continue
        reply = await side.reply(said, agent.task, agent.steps, await agent.view())
        if reply.then == STOP:
            stop(reply.say)
            continue
        if reply.then == CHANGE:
            agent.tell(said)
        await ears.say(reply.say)


async def carry_out(agent: Agent | MacAgent, ears: Ears, side: Side, command: str, names: tuple[str, ...]) -> str:
    """Run one command to a sentence worth speaking, whatever happens."""
    stopped: list[str] = []  # what to say when the user stops the task

    def stop(line: str) -> None:
        stopped.append(line)
        work.cancel()

    work = asyncio.create_task(agent.run(command))
    watcher = asyncio.create_task(listen_while_working(ears, agent, side, stop, names))
    ears.working = True
    try:
        return await work
    except asyncio.CancelledError:
        if asyncio.current_task().cancelling():  # Ctrl-C cancels this task, a spoken stop only the work
            raise
        return stopped[0] if stopped else STOPPED
    except MCPError:  # the server is gone for good: main() says so once and quits
        raise
    except tuple(SPOKEN_ERRORS) as error:
        print(repr(error), file=sys.stderr)
        return spoken_error(error)
    finally:
        ears.working = False
        ears.hush()  # a line the task queued to speak is out of date once it ends
        watcher.cancel()
        work.cancel()


async def converse(ears: Ears, agent: Agent | MacAgent, side: Side, memory: Memory, config: Config) -> None:
    names = config.names
    clock = asyncio.get_running_loop().time
    last_command, remembered = clock(), memory.stamp()
    while True:
        heard = await ears.hear()
        command = addressed(heard, names)
        if command is None:
            continue
        if not heard.pushed:  # a held key already brings the whole utterance
            command = await hear_whole(ears, command)
        while command is not None and not is_dismissal(command, names):
            if not command:
                command = await command_after_name(ears)
                continue
            if clock() - last_command > FORGET_AFTER_SECONDS or memory.stamp() != remembered:
                agent.forget()  # a new conversation, which reads the memory afresh
                remembered = memory.stamp()
            await ears.say(await carry_out(agent, ears, side, command, names))
            last_command = clock()
            if await agent.media_playing():  # a playing video would be heard as follow-up commands
                break
            follow_up = await ears.hear(FOLLOW_UP_SECONDS)
            command = addressed(follow_up, names) if follow_up and follow_up.pushed else await hear_whole(ears, follow_up)
            if command and wake_command(command, names) == "":
                command = ""  # the name alone in the follow-up window is a wake, not a command


def build_agent(config: Config, mode: str, ears: Ears, memory: Memory, headless: bool = False) -> Agent | MacAgent:
    profile_dir = Path(config.profile_dir).expanduser().resolve()
    if profile_dir.is_relative_to(memory.folder):
        raise ConfigError(f"profile_dir must not be inside {memory.folder}: every file in it can be uploaded.")
    profile_dir.mkdir(parents=True, exist_ok=True)
    memory.folder.mkdir(parents=True, exist_ok=True)
    prompt = (MAC_PROMPT if mode == MAC else SYSTEM_PROMPT).format(agent_name=config.agent_name, memory_folder=memory.folder)
    asking = partial(ask, ears, names=config.names)
    if mode == MAC:
        return MacAgent(config.model, memory, prompt, asking, ears.say_later)
    return Agent(config.model, profile_dir, memory, prompt, frozenset(config.blocked_hosts),
                 partial(confirm, ears, names=config.names), asking, ears.say_later, headless,
                 config.extension_token if mode == CHROME else "")


async def main(started: Callable[[Ears], None] = lambda ears: None, mode: str = MAC, wake: bool = True) -> None:
    """Run until cancelled. started receives the Ears once the microphone is ready; mode is what it works in and wake
    whether it listens for its name (both are the menu bar's switches)."""
    config = load_config()
    memory = Memory(MEMORY_NOTES.resolve(), MEMORY_FOLDER.resolve())
    async with Mouth(config.voice, config.voice_model, config.say_voice) as mouth:
        try:
            async with (Ears(config.stt_model, [config.agent_name], mouth, wake) as ears,
                        build_agent(config, mode, ears, memory) as agent, Side(config.quick_model, config.agent_name) as side):
                started(ears)
                greeting, _ = await asyncio.gather(side.greet(memory.notes()), agent.warm_up())
                await ears.say(greeting)
                await converse(ears, agent, side, memory, config)
        except tuple(SPOKEN_ERRORS) as error:  # startup failures and a dead browser server; carry_out speaks the rest
            await mouth.speak(spoken_error(error))
            raise SystemExit(repr(error)) from error  # a string code is printed to the terminal and exits with status 1
        except Exception:
            await mouth.speak(CRASHED)
            raise


if __name__ == "__main__":
    from menubar import run  # imported here so tests and other callers of main() never load AppKit
    run()
