"""Claude operating the whole Mac: it sees the screen and moves the real mouse and keyboard (Anthropic's computer toolset)."""
import asyncio
import base64
import tempfile
from datetime import datetime
from pathlib import Path

import Quartz
from anthropic import DEFAULT_TIMEOUT, Timeout
from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt

from agent import (ASK_TOOL, CACHED, OUTPUT, INTERRUPTED, KEPT_BLOCK_TYPES, MAX_TOKENS, NO_ANSWER, NOW, OUT_OF_STEPS,
                   REQUEST_TIMEOUT_SECONDS, WARMUP, Ask, Conversation, Narrate)
from credit import claude
from memory import Memory
from timing import span

TOOLSET = "computer"
TOOLS = [{"type": "computer_toolset_20260801"}, ASK_TOOL]
MAX_STEPS = 60
KEPT_SCREENS = 10  # older ones turn stale, keeping a prompt under Haiku's 100K-token line
STALE_SCREEN = "(older screenshot removed)"
GLANCE = "glance.png"  # the capture for a quick reply, kept apart from the task's own
OK = "OK"
NOT_EXECUTED = "Not executed: an earlier computer action in this turn failed."
MAX_WAIT_SECONDS = 30
CLICK_GAP_SECONDS = 0.02  # between the events of one click, drag or key press, so apps register them in order
SETTLE_SECONDS = 0.4  # the screen needs a moment to react before the next action or screenshot
TYPE_CHUNK_CHARACTERS = 20  # the most characters one keyboard event carries reliably
LINES_PER_SCROLL_CLICK = 3
CLICKS = {"left_click": (Quartz.kCGMouseButtonLeft, 1), "right_click": (Quartz.kCGMouseButtonRight, 1),
          "middle_click": (Quartz.kCGMouseButtonCenter, 1), "double_click": (Quartz.kCGMouseButtonLeft, 2),
          "triple_click": (Quartz.kCGMouseButtonLeft, 3)}
MOUSE_EVENTS = {Quartz.kCGMouseButtonLeft: (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp),
                Quartz.kCGMouseButtonRight: (Quartz.kCGEventRightMouseDown, Quartz.kCGEventRightMouseUp),
                Quartz.kCGMouseButtonCenter: (Quartz.kCGEventOtherMouseDown, Quartz.kCGEventOtherMouseUp)}
MODIFIERS = {"shift": Quartz.kCGEventFlagMaskShift, "ctrl": Quartz.kCGEventFlagMaskControl,
             "control": Quartz.kCGEventFlagMaskControl, "alt": Quartz.kCGEventFlagMaskAlternate,
             "option": Quartz.kCGEventFlagMaskAlternate, "super": Quartz.kCGEventFlagMaskCommand,
             "cmd": Quartz.kCGEventFlagMaskCommand, "command": Quartz.kCGEventFlagMaskCommand,
             "meta": Quartz.kCGEventFlagMaskCommand}
SCROLLS = {"up": (1, 0), "down": (-1, 0), "left": (0, 1), "right": (0, -1)}
KEY_CODES = {  # macOS virtual key codes on the ANSI layout, by the lower-case names the model uses
    **dict(zip("asdfhgzxcv", range(10), strict=True)), "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
    "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "equal": 24, "9": 25, "7": 26, "-": 27, "minus": 27,
    "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "return": 36, "enter": 36, "l": 37, "j": 38,
    "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "comma": 43, "/": 44, "slash": 44, "n": 45, "m": 46, ".": 47,
    "period": 47, "tab": 48, "space": 49, " ": 49, "`": 50, "grave": 50, "backspace": 51, "escape": 53, "esc": 53,
    "delete": 117, "home": 115, "end": 119, "page_up": 116, "pageup": 116, "prior": 116, "page_down": 121,
    "pagedown": 121, "next": 121, "left": 123, "right": 124, "down": 125, "up": 126, "f1": 122, "f2": 120, "f3": 99,
    "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111}
NO_PERMISSION = ("macOS is not letting this terminal {missing}. Allow it in System Settings, Privacy and Security, "
                 "then quit and reopen the terminal and start Beacon again.")


class MacPermissionError(Exception):
    """The terminal lacks Screen Recording or Accessibility permission, so the screen cannot be seen or controlled."""


class MacActionError(Exception):
    """One action could not be carried out; the model reads the message and tries another way."""


def _flags(modifiers: str | None) -> int:
    flags = 0
    for name in filter(None, (modifiers or "").lower().split("+")):
        if name not in MODIFIERS:
            raise MacActionError(f"Unknown modifier {name!r}. Use shift, ctrl, alt or super.")
        flags |= MODIFIERS[name]
    return flags


def _post(event, flags: int = 0) -> None:
    Quartz.CGEventSetFlags(event, flags)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def _pointer() -> tuple[float, float]:
    return tuple(Quartz.CGEventGetLocation(Quartz.CGEventCreate(None)))


class Mac:
    """The real screen, mouse and keyboard, in the pixel space of the screenshots it returns."""

    def __init__(self) -> None:
        display = Quartz.CGMainDisplayID()
        self._width, self._height = Quartz.CGDisplayPixelsWide(display), Quartz.CGDisplayPixelsHigh(display)  # in points
        self._folder = Path(tempfile.mkdtemp(prefix="beacon-screen-"))

    async def capture(self, region: tuple[float, float, float, float] | None = None, name: str = "screen.png") -> dict:
        """A PNG of the main display, or of a (left, top, right, bottom) region of it magnified, scaled to fit the
        display's size in points, as an image block. A different name lets a second capture run alongside the task's."""
        shot = self._folder / name
        left, top, right, bottom = region or (0, 0, self._width, self._height)
        width, height = max(right - left, 1), max(bottom - top, 1)
        fitted = round(width * min(self._width / width, self._height / height))  # a tall region must not outgrow the screen
        region_arguments = ["-R", f"{left},{top},{width},{height}"] if region else []
        for command in (["screencapture", "-x", "-m", "-t", "png", *region_arguments, str(shot)],
                        ["sips", "--resampleWidth", str(fitted), str(shot)]):
            process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.DEVNULL,
                                                           stderr=asyncio.subprocess.DEVNULL)
            if await process.wait():
                raise MacActionError("The screen could not be captured.")
        data = base64.standard_b64encode(shot.read_bytes()).decode()
        return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}

    async def _mouse(self, kind: int, point, button: int = Quartz.kCGMouseButtonLeft, clicks: int = 1, flags: int = 0) -> None:
        event = Quartz.CGEventCreateMouseEvent(None, kind, tuple(point), button)
        Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventClickState, clicks)
        _post(event, flags)
        await asyncio.sleep(CLICK_GAP_SECONDS)

    async def _key(self, combination: str, down: bool = True, up: bool = True) -> None:
        *modifiers, key = combination.split("+") if combination != "+" else ["+"]
        flags = _flags("+".join(modifiers))
        if key.lower() in MODIFIERS:  # a bare modifier such as "shift": nothing to press beyond the flag
            return
        if key.lower() not in KEY_CODES:
            raise MacActionError(f"Unknown key {key!r}. Use names such as Return, Tab, space, Escape, BackSpace, Up, a, 1, F5.")
        for pressed in ([True] if down else []) + ([False] if up else []):
            _post(Quartz.CGEventCreateKeyboardEvent(None, KEY_CODES[key.lower()], pressed), flags)
            await asyncio.sleep(CLICK_GAP_SECONDS)

    async def act(self, name: str, arguments: dict) -> list[dict]:
        """Carry out one member of the computer toolset and return the content of its tool result."""
        point = arguments.get("coordinate") or _pointer()
        flags = _flags(arguments.get("text")) if name in CLICKS or name in ("left_click_drag", "scroll") else 0
        if name == "screenshot":
            return [await self.capture()]
        if name == "zoom":
            return [await self.capture(tuple(arguments["region"]))]
        if name == "cursor_position":
            return [{"type": "text", "text": "x={:.0f}, y={:.0f}".format(*_pointer())}]
        if name == "wait":
            await asyncio.sleep(min(arguments["duration"], MAX_WAIT_SECONDS))
        elif name in CLICKS:
            button, count = CLICKS[name]
            await self._mouse(Quartz.kCGEventMouseMoved, point)
            for click in range(1, count + 1):
                for kind in MOUSE_EVENTS[button]:
                    await self._mouse(kind, point, button, click, flags)
        elif name == "mouse_move":
            await self._mouse(Quartz.kCGEventMouseMoved, point)
        elif name == "left_click_drag":
            await self._mouse(Quartz.kCGEventLeftMouseDown, arguments["start_coordinate"], flags=flags)
            await self._mouse(Quartz.kCGEventLeftMouseDragged, point, flags=flags)
            await self._mouse(Quartz.kCGEventLeftMouseUp, point, flags=flags)
        elif name in ("left_mouse_down", "left_mouse_up"):
            await self._mouse(MOUSE_EVENTS[Quartz.kCGMouseButtonLeft][name == "left_mouse_up"], point)
        elif name == "scroll":
            await self._mouse(Quartz.kCGEventMouseMoved, point)
            vertical, horizontal = (step * arguments["scroll_amount"] * LINES_PER_SCROLL_CLICK
                                    for step in SCROLLS[arguments["scroll_direction"]])
            _post(Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitLine, 2, vertical, horizontal), flags)
        elif name == "type":
            for line_number, line in enumerate(arguments["text"].split("\n")):
                if line_number:
                    await self._key("Return")
                for start in range(0, len(line), TYPE_CHUNK_CHARACTERS):
                    for pressed in (True, False):
                        event = Quartz.CGEventCreateKeyboardEvent(None, 0, pressed)
                        chunk = line[start:start + TYPE_CHUNK_CHARACTERS]
                        Quartz.CGEventKeyboardSetUnicodeString(event, len(chunk), chunk)
                        _post(event)
                    await asyncio.sleep(CLICK_GAP_SECONDS)
        elif name == "key":
            for _ in range(arguments.get("repeat", 1)):
                await self._key(arguments["text"])
        elif name == "hold_key":
            await self._key(arguments["text"], up=False)
            await asyncio.sleep(min(arguments["duration"], MAX_WAIT_SECONDS))
            await self._key(arguments["text"], down=False)
        else:
            raise MacActionError(f"Unknown action {name!r}.")
        await asyncio.sleep(SETTLE_SECONDS)
        return [{"type": "text", "text": OK}]


def request_permissions() -> None:
    """Show macOS's Screen Recording and Accessibility prompts for whatever is still missing; they do not block."""
    if not Quartz.CGPreflightScreenCaptureAccess():
        Quartz.CGRequestScreenCaptureAccess()
    AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})


def check_permissions() -> None:
    """Raise MacPermissionError, after asking macOS to show its prompt, when the screen cannot be seen or controlled."""
    missing = []
    if not Quartz.CGPreflightScreenCaptureAccess():
        Quartz.CGRequestScreenCaptureAccess()
        missing.append("record the screen (Screen Recording)")
    if not AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}):
        missing.append("control the mouse and keyboard (Accessibility)")
    if missing:
        raise MacPermissionError(NO_PERMISSION.format(missing=" or ".join(missing)))


class MacAgent(Conversation):
    """One conversation that operates the whole Mac. Same surface as agent.Agent, so the voice loop does not care which."""

    def __init__(self, model: str, memory: Memory, system: list[dict], ask: Ask, narrate: Narrate) -> None:
        super().__init__(memory, narrate)
        self._model, self._ask = model, ask
        self._system = system
        self._anthropic = claude(timeout=Timeout(REQUEST_TIMEOUT_SECONDS, connect=DEFAULT_TIMEOUT.connect))
        self._screens: list[dict] = []

    async def __aenter__(self) -> "MacAgent":
        check_permissions()
        self._mac = Mac()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self._anthropic.close()

    async def warm_up(self) -> None:
        await self._anthropic.messages.create(model=self._model, max_tokens=0, system=self._system, tools=TOOLS,
                                              output_config=OUTPUT,
                                              messages=[{"role": "user", "content": WARMUP}])

    def forget(self) -> None:
        self._messages.clear()
        self._screens.clear()

    async def media_playing(self) -> bool:
        return False  # not knowable from here; the follow-up window stays open

    async def view(self) -> list[dict]:
        """What is on screen right now, captured beside whatever the task is doing."""
        return [await self._mac.capture(name=GLANCE)]

    async def run(self, command: str) -> str:
        """Carry out one spoken command and return the sentence to speak back. Cancel the task to interrupt it."""
        self._keep_screens(0)  # images are large: a command keeps only its own latest screenshots
        try:
            asked = f"{command}\n\n{NOW.format(now=datetime.now())}"
            self._messages.append(self._opening(command, [{"type": "text", "text": asked}]))
            for _ in range(MAX_STEPS):
                reply = await self._step(self._anthropic.messages.create(
                    model=self._model, max_tokens=MAX_TOKENS, system=self._system, tools=TOOLS, output_config=OUTPUT,
                    messages=self._messages, cache_control=CACHED))
                if reply is None:
                    continue
                if reply.stop_reason != "tool_use":
                    if (answer := self._finished(reply)) is not None:
                        return answer
                    continue
                kept = [block for block in reply.content if block.type in KEPT_BLOCK_TYPES]
                self._took(kept)
                results = await self._results([block for block in kept if block.type == "tool_use"])
                self._messages += [{"role": "assistant", "content": kept},
                                   {"role": "user", "content": results + self._heard_blocks()}]
        except BaseException:
            self._messages.append({"role": "user", "content": INTERRUPTED})
            raise
        return OUT_OF_STEPS

    def _keep_screens(self, count: int) -> None:
        """Turn all but the newest count screenshots into a short note."""
        stale = self._screens[:len(self._screens) - count]
        for screen in stale:
            screen.clear()
            screen.update(type="text", text=STALE_SCREEN)
        del self._screens[:len(stale)]

    async def _results(self, calls: list) -> list[dict]:
        """One tool result per call, in order; after a failed computer action the rest of the batch is not run."""
        results, failed = [], False
        for call in calls:
            result = {"type": "tool_result", "tool_use_id": call.id}
            if call.name == ASK_TOOL["name"]:
                answer = await self._ask(str(call.input.get("question", "")))
                result["content"] = f"The user said: {answer}" if answer else NO_ANSWER
            else:
                result["toolset_name"] = TOOLSET
                try:
                    if failed:
                        raise MacActionError(NOT_EXECUTED)
                    with span("computer", action=call.name):
                        result["content"] = await self._mac.act(call.name, call.input)
                    self._screens += [block for block in result["content"] if block["type"] == "image"]
                    self._keep_screens(KEPT_SCREENS)
                except (MacActionError, KeyError, TypeError, ValueError) as error:  # KeyError and co: a malformed call
                    failed = True
                    result |= {"is_error": True, "content": f"Error: {error}"}
            results.append(result)
        return results
