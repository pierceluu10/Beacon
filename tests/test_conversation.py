"""Memory, the menu's saved settings, the quick side replies, the ElevenLabs voice and its fallback, and how a
running task takes the user's words and speaks its first line. No microphone, no API request, no sound."""
import asyncio
import base64
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import httpx2
import pytest
from anthropic.types import Message

import credit
import mac
import main
import memory
import menubar
import mouth
import side
from agent import CACHED, INTERRUPTED, SAID_STEP, SNAPSHOT_TOOL, USER_SAID
from gate import url_refusal
from memory import Memory

NEVER_SENT = "never sent: these tests make no API request"
NOTES = "My name is Sam. Keep replies short."
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
PDF = b"%PDF-1.4\n%%EOF\n"
SCREEN = [{"type": "text", "text": "a screenshot"}]
SLOW_STEP_SECONDS = 30  # a model step that only ends when it is dropped
STEP_WAIT_SECONDS = 1
LINKEDIN_JOB = "https://www.linkedin.com/jobs/view/41"


def reply(stop_reason: str, *content: dict) -> Message:
    return Message.model_validate({"id": "msg_scripted", "type": "message", "role": "assistant", "model": NEVER_SENT,
                                   "stop_reason": stop_reason, "usage": {"input_tokens": 0, "output_tokens": 0},
                                   "content": list(content)})


def text(words: str) -> dict:
    return {"type": "text", "text": words}


def tool_use(name: str, arguments: dict, number: int = 0) -> dict:
    return {"type": "tool_use", "id": f"toolu_{number}", "name": name, "input": arguments}


@pytest.fixture
def remembered(tmp_path: Path) -> Memory:
    return Memory(tmp_path / "memory.md", tmp_path / "memory")


def test_an_empty_memory_adds_nothing(remembered: Memory) -> None:
    assert remembered.blocks() == [] and remembered.notes() == ""


def test_memory_holds_the_notes_and_each_file_in_a_form_the_api_reads(remembered: Memory) -> None:
    remembered.notes_path.write_text(NOTES)
    remembered.folder.mkdir()
    (remembered.folder / "about.txt").write_text("I live in Toronto.")
    (remembered.folder / "photo.png").write_bytes(PNG)
    (remembered.folder / "resume.pdf").write_bytes(PDF)
    (remembered.folder / "archive.zip").write_bytes(b"PK\x03\x04\xff\xfe")
    (remembered.folder / ".DS_Store").write_bytes(b"\x00")
    blocks = remembered.blocks()
    kinds = [block["type"] for block in blocks]
    texts = " | ".join(block["text"] for block in blocks if block["type"] == "text")
    assert kinds == ["text", "text", "text", "text", "text", "image", "text", "document"], kinds
    assert NOTES in texts and "I live in Toronto." in texts and "archive.zip (not readable here" in texts, texts
    assert ".DS_Store" not in texts, "a hidden file was sent"
    assert blocks[5]["source"]["media_type"] == "image/png" and blocks[7]["source"]["media_type"] == "application/pdf"


def test_a_file_too_large_to_send_is_named_but_not_read(remembered: Memory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(memory, "MAX_FILE_BYTES", 4)
    remembered.folder.mkdir()
    (remembered.folder / "big.txt").write_text("more than four bytes")
    blocks = remembered.blocks()
    assert len(blocks) == 2 and "big.txt (not readable here" in blocks[1]["text"], blocks


def test_the_memory_stamp_changes_with_the_notes_and_the_files(remembered: Memory) -> None:
    before = remembered.stamp()
    remembered.notes_path.write_text(NOTES)
    with_notes = remembered.stamp()
    remembered.folder.mkdir()
    (remembered.folder / "a.txt").write_text("a")
    assert len({before, with_notes, remembered.stamp()}) == 3


def test_settings_start_from_the_defaults_and_keep_only_known_keys(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    assert menubar.load_settings(path) == menubar.DEFAULT_SETTINGS
    path.write_text(json.dumps({"wake": False, "mode": "window", "volume": 11}))
    loaded = menubar.load_settings(path)
    assert loaded == {"mode": "window", "wake": False, "hotkey": menubar.DEFAULT_HOTKEY}, loaded


def test_menu_choices_are_saved_and_reach_the_running_ears(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(menubar, "SETTINGS", tmp_path / "settings.json")
    monkeypatch.setattr(menubar.save_settings, "__defaults__", (tmp_path / "settings.json",))
    beacon = menubar.Beacon(dict(menubar.DEFAULT_SETTINGS))
    calls = []
    beacon._loop = SimpleNamespace(call_soon_threadsafe=lambda method, *arguments: calls.append((method, arguments)))
    beacon._ears = SimpleNamespace(activity="ready", set_wake="set_wake", push="push")
    beacon._thread = SimpleNamespace(is_alive=lambda: True)
    chosen = {"code": 96, "modifiers": 0, "label": "F5"}
    for message in ({"action": "wake"}, {"action": "hotkey", "hotkey": chosen}, {"action": "push", "down": True}):
        beacon.act(message)
    saved = json.loads((tmp_path / "settings.json").read_text())
    assert saved == {"mode": "mac", "wake": False, "hotkey": chosen}, saved
    assert calls == [("set_wake", (False,)), ("push", (True,))], calls


def test_while_starting_or_off_the_key_reaches_nothing() -> None:
    beacon = menubar.Beacon(dict(menubar.DEFAULT_SETTINGS))
    beacon._loop = SimpleNamespace(call_soon_threadsafe=lambda *call: pytest.fail("reached a loop that is not running"))
    beacon.act({"action": "push", "down": True})  # off: no thread at all
    beacon._thread = SimpleNamespace(is_alive=lambda: True)
    assert beacon.state == menubar.STARTING
    beacon.act({"action": "push", "down": True})


class ScriptedAnthropic:
    """Stands in for AsyncAnthropic: plays the given replies and keeps a copy of every request."""

    def __init__(self, *replies: Message) -> None:
        self.requests: list[dict] = []
        self._replies = list(replies)
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **request: object) -> Message:
        self.requests.append(copy.deepcopy(request))
        return self._replies.pop(0)

    async def close(self) -> None:
        pass


def test_a_quick_reply_is_read_from_the_forced_tool_and_sees_the_screen() -> None:
    quick = side.Side(NEVER_SENT, "Beacon")
    quick._anthropic = ScriptedAnthropic(
        reply("tool_use", tool_use("reply", {"say": "Your inbox, with three unread.", "then": "continue"})),
        reply("tool_use", tool_use("reply", {"say": "Okay.", "then": "rethink"})))
    answer = asyncio.run(quick.reply("what are you seeing?", "check my email", ["screenshot {}"], SCREEN))
    request = quick._anthropic.requests[0]
    assert answer == side.Reply("Your inbox, with three unread.", side.CONTINUE), answer
    assert request["tool_choice"] == {"type": "tool", "name": "reply"}, request["tool_choice"]
    assert request["messages"][0]["content"][0] == SCREEN[0] and "what are you seeing?" in request["messages"][0]["content"][1]["text"]
    unknown = asyncio.run(quick.reply("hm", "check my email", [], []))
    assert unknown.then == side.CONTINUE, "an unknown decision must leave the task alone"


def test_the_greeting_is_written_from_the_notes() -> None:
    quick = side.Side(NEVER_SENT, "Beacon")
    quick._anthropic = ScriptedAnthropic(reply("end_turn", text("Hey Sam, what are we doing today?")),
                                         reply("end_turn", text("Hey, what are we doing today?")))
    assert asyncio.run(quick.greet(NOTES)) == "Hey Sam, what are we doing today?"
    asyncio.run(quick.greet(""))
    sent = [request["messages"][0]["content"] for request in quick._anthropic.requests]
    assert NOTES in sent[0] and sent[1] == side.NO_NOTES, sent
    assert all(request["thinking"] == side.NO_THINKING for request in quick._anthropic.requests), "thinking would eat the greeting"


class ScriptedMac:
    """Records actions; on the first one, the user speaks to the running task."""

    def __init__(self, driver: mac.MacAgent, words: str) -> None:
        self.actions: list[str] = []
        self._driver, self._words = driver, words

    async def act(self, name: str, arguments: dict) -> list[dict]:
        if not self.actions:
            self._driver.tell(self._words)
        self.actions.append(name)
        return [{"type": "text", "text": mac.OK}]


def test_a_running_task_speaks_its_first_line_reads_memory_once_and_takes_words_said_mid_task(remembered: Memory) -> None:
    remembered.notes_path.write_text(NOTES)
    narrated: list[str] = []
    driver = mac.MacAgent(NEVER_SENT, remembered, "prompt", lambda question: None, narrated.append)
    driver._mac = ScriptedMac(driver, "use Gmail, not Outlook")
    driver._anthropic = ScriptedAnthropic(
        reply("tool_use", text("Opening your mail."), tool_use("left_click", {"coordinate": [1, 2]}, 1)),
        reply("tool_use", text("Switching to Gmail."), tool_use("key", {"text": "cmd+t"}, 2)),
        reply("end_turn", text("Gmail is open.")),
        reply("end_turn", text("Done.")))
    answer = asyncio.run(driver.run("check my mail"))
    requests = driver._anthropic.requests
    second_turn = requests[1]["messages"][-1]["content"]
    assert answer == "Gmail is open.", answer
    assert narrated == ["Opening your mail."], f"spoken mid-task: {narrated}"
    assert second_turn[-1] == text(USER_SAID.format(words="use Gmail, not Outlook")), second_turn
    assert driver.steps[0] == SAID_STEP.format(text="Opening your mail.") and driver.steps[1].startswith("left_click")
    asyncio.run(driver.run("thanks"))
    openings = [message["content"] for message in driver._messages if message["role"] == "user"
                and isinstance(message["content"], list) and message["content"][0].get("text") == memory.HEADER]
    assert len(openings) == 1, "the memory was sent again in the same conversation"


def test_words_that_arrive_as_the_task_finishes_carry_it_on(remembered: Memory) -> None:
    driver = mac.MacAgent(NEVER_SENT, remembered, "prompt", lambda question: None, lambda line: None)
    driver._anthropic = ScriptedAnthropic(reply("end_turn", text("Opened Notes.")), reply("end_turn", text("Added it.")))
    original = driver._anthropic.messages.create

    async def told_during_the_call(**request: object) -> Message:
        answer = await original(**request)
        if len(driver._anthropic.requests) == 1:
            driver.tell("and add milk")
        return answer

    driver._anthropic.messages.create = told_during_the_call
    answer = asyncio.run(driver.run("open Notes"))
    assert answer == "Added it.", f"the user's late words were dropped and it said {answer!r}"
    assert driver._anthropic.requests[1]["messages"][-1]["content"] == [text(USER_SAID.format(words="and add milk"))]


def writing_forever(driver: mac.MacAgent) -> asyncio.Event:
    """Makes the driver's first model request hang until cancelled; later ones are scripted. Returns the event set once
    that first request is being written."""
    writing, scripted = asyncio.Event(), driver._anthropic.messages.create

    async def create(**request: object) -> Message:
        if not writing.is_set():
            writing.set()
            await asyncio.sleep(SLOW_STEP_SECONDS)
        return await scripted(**request)

    driver._anthropic.messages.create = create
    return writing


def test_words_said_while_the_model_writes_a_step_drop_that_step_and_reach_the_model_at_once(remembered: Memory) -> None:
    driver = mac.MacAgent(NEVER_SENT, remembered, "prompt", lambda question: None, lambda line: None)
    driver._anthropic = ScriptedAnthropic(reply("end_turn", text("Opened Gmail.")))
    writing = writing_forever(driver)

    async def scenario() -> str:
        task = asyncio.create_task(driver.run("open Outlook"))
        await writing.wait()
        driver.tell("use Gmail instead")
        return await asyncio.wait_for(task, STEP_WAIT_SECONDS)

    answer = asyncio.run(scenario())
    sent = driver._anthropic.requests
    assert answer == "Opened Gmail.", answer
    assert len(sent) == 1 and sent[0]["messages"][-1]["content"] == [text(USER_SAID.format(words="use Gmail instead"))], sent


def test_a_stop_while_the_model_writes_a_step_ends_the_task(remembered: Memory) -> None:
    driver = mac.MacAgent(NEVER_SENT, remembered, "prompt", lambda question: None, lambda line: None)
    driver._anthropic = ScriptedAnthropic()
    writing = writing_forever(driver)

    async def scenario() -> bool:
        task = asyncio.create_task(driver.run("open Outlook"))
        await writing.wait()
        task.cancel()
        await asyncio.wait({task}, timeout=STEP_WAIT_SECONDS)
        return task.cancelled()

    assert asyncio.run(scenario()), "a stop was taken for words said mid-task"
    assert driver._messages[-1] == {"role": "user", "content": INTERRUPTED}, driver._messages[-1]


@pytest.mark.parametrize("blocked_hosts", [[], ["bank.example"]], ids=["the default config", "a config that blocks others"])
def test_linkedin_is_blocked_whatever_the_config_blocks(tmp_path: Path, blocked_hosts: list[str]) -> None:
    config = main.Config(blocked_hosts=blocked_hosts)
    driver = main.build_agent(config, main.WINDOW, SimpleNamespace(say_later=print),
                              Memory(tmp_path / "memory.md", tmp_path / "memory"), profile_dir=tmp_path / "profile")
    assert url_refusal(LINKEDIN_JOB, driver._blocked_hosts), f"{LINKEDIN_JOB} opens with {driver._blocked_hosts}"
    assert set(blocked_hosts) <= driver._blocked_hosts, driver._blocked_hosts


def test_a_redirect_into_a_blocked_site_closes_the_page(tmp_path: Path) -> None:
    """Navigation is checked before it starts; a link or redirect that lands on a blocked site is caught on arrival."""
    driver = main.build_agent(main.Config(blocked_hosts=["bank.example"]), main.WINDOW, SimpleNamespace(say_later=print),
                              Memory(tmp_path / "memory.md", tmp_path / "memory"), profile_dir=tmp_path / "profile")
    calls = []

    async def call(tool: str, arguments: dict) -> str:
        calls.append((tool, arguments))
        return "### Page\n- Page URL: https://login.bank.example/account\n- Page Title: Bank\n" if tool == SNAPSHOT_TOOL else ""

    driver._call = call
    shown = asyncio.run(driver._page())
    print(shown, calls)
    assert "login.bank.example" in shown and "blocked" in shown and "Page Title: Bank" not in shown
    assert ("browser_navigate", {"url": "about:blank"}) in calls and not driver._on_page


def stream(handler, key: str = "key") -> mouth.Mouth:
    speaker = mouth.Mouth()
    speaker._key = key
    speaker._http = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return speaker


def fake_speakers(played: list) -> type:
    """sounddevice's output stream, recording its settings, each write, and the stop."""
    class Speakers:
        def __init__(self, **settings: object) -> None:
            played.append(settings)

        def start(self) -> None: pass
        def write(self, audio: bytes) -> None: played.append(audio)
        def stop(self) -> None: played.append("stop")
        def abort(self) -> None: pass
        def close(self) -> None: pass

    return Speakers


@pytest.mark.parametrize(("status", "keeps_key"), [(401, False), (402, False), (500, True)])
def test_a_refused_line_is_spoken_by_macos_and_a_dead_key_is_not_tried_again(
        monkeypatch: pytest.MonkeyPatch, status: int, keeps_key: bool) -> None:
    said = []

    async def say(text: str, voice: str) -> None:
        said.append((text, voice))

    monkeypatch.setattr(mouth, "say", say)
    voice = stream(lambda request: httpx2.Response(status))
    asyncio.run(voice.speak("Hello."))
    assert said == [("Hello.", "Samantha")] and voice._voiced is keeps_key, (said, voice._voiced)


def test_the_elevenlabs_request_asks_for_raw_audio_in_the_chosen_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    sent, played = [], []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return httpx2.Response(200, content=b"\x01\x02\x03\x04\x05")  # an odd byte count: half a sample at the end

    monkeypatch.setattr(mouth.sounddevice, "RawOutputStream", fake_speakers(played))
    asyncio.run(stream(handler).speak("Hello."))
    request = sent[0]
    assert request.url.path == f"/v1/text-to-speech/{mouth.ELEVENLABS_VOICE}/stream" and request.url.params["output_format"] == mouth.OUTPUT_FORMAT
    assert request.headers["xi-api-key"] == "key" and json.loads(request.content) == {
        "text": "Hello.", "model_id": "eleven_v4_turbo", "voice_settings": {"stability": mouth.ELEVENLABS_STABILITY}}
    *audio, tail, stopped = played[1:]
    assert played[0]["samplerate"] == mouth.OUTPUT_RATE and b"".join(audio) == b"\x01\x02\x03\x04", played
    # the speakers crackle when stopped right after the voice, so half a second of silence plays first
    assert tail == bytes(len(tail)) and len(tail) == mouth.OUTPUT_RATE // 2 * mouth.SAMPLE_BYTES and stopped == "stop", played


def test_without_a_key_the_voice_goes_through_beacons_server_as_this_mac(monkeypatch: pytest.MonkeyPatch,
                                                                         tmp_path: Path) -> None:
    monkeypatch.setattr(credit, "DEVICE_FILE", tmp_path / "device")
    sent, played = [], []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return httpx2.Response(200, content=b"\x01\x02")

    monkeypatch.setattr(mouth.sounddevice, "RawOutputStream", fake_speakers(played))
    asyncio.run(stream(handler, key="").speak("Hello."))
    request = sent[0]
    print(request.url, dict(request.headers))
    assert str(request.url).startswith(f"{credit.SERVER}/v1/text-to-speech/{mouth.ELEVENLABS_VOICE}/stream")
    assert request.headers["x-api-key"] == credit.device_id() and "xi-api-key" not in request.headers


@pytest.mark.parametrize("mode", [main.WINDOW, main.MAC])
def test_every_prompt_that_writes_spoken_words_carries_the_speaking_style(tmp_path: Path, mode: str) -> None:
    """The task prompts, the greeting and the mid-task replies all write words that are spoken: they must sound the same."""
    built = main.build_agent(main.Config(), mode, SimpleNamespace(say_later=None), Memory(tmp_path / "notes.md", tmp_path / "files"),
                             profile_dir=tmp_path / "profile")
    for prompt in (built._system[-1]["text"], side.GREETING_PROMPT, side.REPLY_PROMPT):
        assert mouth.SPEAKING_STYLE in prompt, prompt


@pytest.mark.parametrize("key", ["", "own-key"], ids=["on Beacon's server", "on your own key"])
def test_on_beacons_server_the_prompt_never_reaches_this_mac(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, key: str) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    prompt, settings = main.system_prompt("browser", "Beacon", tmp_path)
    print(prompt, settings, sep="\n")
    assert prompt == {"type": "text", "text": "<beacon:browser>" if not key else main.PUBLIC_PROMPTS["browser"]}, prompt
    assert settings["cache_control"] == CACHED and "Your name is Beacon" in settings["text"], "the prompt and settings are cached as one"


def test_a_task_keeps_only_its_latest_screenshots(remembered: Memory) -> None:
    driver = mac.MacAgent(NEVER_SENT, remembered, [], lambda question: None, lambda line: None)
    screens = [{"type": "image", "source": number} for number in range(3)]
    driver._screens = list(screens)
    driver._keep_screens(2)
    assert screens[0] == {"type": "text", "text": mac.STALE_SCREEN} and screens[1:] == driver._screens, screens
    assert [screen["source"] for screen in driver._screens] == [1, 2], driver._screens


@pytest.mark.parametrize("region", [(0, 0, 200, 351), (10, 10, 900, 60), None], ids=["tall zoom", "wide zoom", "screen"])
def test_captures_fit_inside_the_screen_size_so_the_api_accepts_them(region):
    """A zoom used to be stretched to the screen's width, so a tall region came back 1512 x 2652 and the API refused it."""
    screen = mac.Mac()
    block = asyncio.run(screen.capture(region))
    png = base64.b64decode(block["source"]["data"])
    width, height = int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")
    print(f"{region} -> {width} x {height} (screen {screen._width} x {screen._height})")
    assert width <= screen._width and height <= screen._height + 1
    left, top, right, bottom = region or (0, 0, screen._width, screen._height)
    assert abs(width / height - (right - left) / (bottom - top)) < 0.05, "the aspect ratio changed"
