"""Beacon: say the agent's name, speak a command, watch Chrome do it."""
import asyncio
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field, fields, replace
from functools import partial
from pathlib import Path

import anthropic
import sounddevice
from mcp import MCPError

from agent import Agent, BrowserClosedError, BrowserStartError
from mac import MacAgent, MacPermissionError
from voice import Ears, MicrophoneSilentError, is_dismissal, is_stop, parse_yes_no, speak, wake_command

HOME = Path("~/.beacon").expanduser()
CONFIG_PATH = HOME / "config.toml"
CONTINUATION_SECONDS = 0.8  # a pause mid-sentence puts 0.47 to 0.52 s between its two transcripts; 0.6 missed some under load
FOLLOW_UP_SECONDS = 8
CONFIRM_SECONDS = 8
CONFIRM_ATTEMPTS = 2
ASK_SECONDS = 180
FORGET_AFTER_SECONDS = 600
READY = "Ready."
GREETING = "Hi {user_name}."
ACKNOWLEDGEMENT = "On it."
STOPPED = "Stopped."
CONFIRM_RETRY = "Sorry, yes or no?"
CRASHED = "Something broke and I have to quit. The details are in the terminal."
NO_MICROPHONE = "I can't hear the microphone. Allow it for this terminal in System Settings, then start me again."
NO_FACTS = "(none provided)"
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
    user_name: str = "Pierce"
    model: str = "claude-opus-5-5"
    profile_dir: str = str(HOME / "chrome-profile")
    resume_path: str = str(HOME / "uploads" / "resume.pdf")
    facts_path: str = str(HOME / "facts.md")
    blocked_hosts: list[str] = field(default_factory=list)
    stt_model: str = "MEDIUM_STREAMING"
    say_voice: str = "Samantha"
    mode: str = WINDOW  # WINDOW, CHROME (needs extension_token) or MAC
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


async def stop_when_told(ears: Ears, work: asyncio.Task, names: tuple[str, ...]) -> None:
    while True:
        heard = await ears.hear()
        if is_stop(wake_command(heard, names) or heard, names):
            work.cancel()


async def carry_out(agent: Agent, ears: Ears, command: str, names: tuple[str, ...]) -> str:
    """Run one command to a sentence worth speaking, whatever happens."""
    work = asyncio.create_task(agent.run(command))
    watcher = asyncio.create_task(stop_when_told(ears, work, names))
    try:
        await ears.say(ACKNOWLEDGEMENT)  # overlaps the first model call
        return await work
    except asyncio.CancelledError:
        if asyncio.current_task().cancelling():  # Ctrl-C cancels this task, a spoken stop only the work
            raise
        return STOPPED
    except MCPError:  # the server is gone for good: main() says so once and quits
        raise
    except tuple(SPOKEN_ERRORS) as error:
        print(repr(error), file=sys.stderr)
        return spoken_error(error)
    finally:
        watcher.cancel()
        work.cancel()


async def converse(ears: Ears, agent: Agent, config: Config) -> None:
    names = config.names
    clock = asyncio.get_running_loop().time
    last_command = clock()
    while True:
        command = wake_command(await ears.hear(), names)
        if command is None:
            continue
        command = await hear_whole(ears, command)
        while command is not None and not is_dismissal(command, names):
            if command:
                if clock() - last_command > FORGET_AFTER_SECONDS:
                    agent.forget()
                await ears.say(await carry_out(agent, ears, command, names))
                last_command = clock()
                if await agent.media_playing():  # a playing video would be heard as follow-up commands
                    break
            else:
                await ears.say(GREETING.format(user_name=config.user_name))
            command = await hear_whole(ears, await ears.hear(FOLLOW_UP_SECONDS))
            if command and wake_command(command, names) == "":
                command = ""  # the name alone in the follow-up window is a wake, not a command


def build_agent(config: Config, ears: Ears, headless: bool = False) -> Agent | MacAgent:
    profile_dir, resume_path, facts_path = (Path(value).expanduser().resolve() for value in
                                            (config.profile_dir, config.resume_path, config.facts_path))
    upload_dir = resume_path.parent
    if profile_dir.is_relative_to(upload_dir):
        raise ConfigError(f"profile_dir must not be inside {upload_dir}: every file in the resume's folder can be uploaded.")
    profile_dir.mkdir(parents=True, exist_ok=True)
    upload_dir.mkdir(parents=True, exist_ok=True)
    prompt = (MAC_PROMPT if config.mode == MAC else SYSTEM_PROMPT).format(
        agent_name=config.agent_name, user_name=config.user_name, upload_dir=upload_dir, resume_name=resume_path.name,
        uploads=", ".join(sorted(file.name for file in upload_dir.iterdir() if file.is_file())) or NO_FACTS,
        facts=facts_path.read_text() if facts_path.exists() else NO_FACTS)
    if config.mode == MAC:
        return MacAgent(config.model, prompt, partial(ask, ears, names=config.names))
    return Agent(config.model, profile_dir, upload_dir, prompt, frozenset(config.blocked_hosts),
                 partial(confirm, ears, names=config.names), partial(ask, ears, names=config.names), headless,
                 config.extension_token if config.mode == CHROME else "")


async def main(started: Callable[[Ears], None] = lambda ears: None, mode: str | None = None) -> None:
    """Run until cancelled. started receives the Ears once the microphone is live; mode overrides the configured one
    (both are the menu bar's switches)."""
    config = load_config()
    if mode:
        config = replace(config, mode=mode)
    try:
        async with Ears(config.stt_model, [config.agent_name], config.say_voice) as ears, build_agent(config, ears) as agent:
            started(ears)
            await agent.warm_up()
            await ears.say(READY)
            await converse(ears, agent, config)
    except tuple(SPOKEN_ERRORS) as error:  # startup failures and a dead browser server; carry_out speaks the rest
        await speak(spoken_error(error), config.say_voice)
        raise SystemExit(repr(error)) from error  # a string code is printed to the terminal and exits with status 1
    except Exception:
        await speak(CRASHED, config.say_voice)
        raise


if __name__ == "__main__":
    from menubar import run  # imported here so tests and other callers of main() never load AppKit
    run()
