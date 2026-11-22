"""Claude driving a visible Chrome through the Playwright MCP server. Every browser call passes the gate first."""
import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from datetime import datetime
from pathlib import Path

from anthropic import DEFAULT_TIMEOUT, AsyncAnthropic, Timeout
from anthropic.types import Message
from mcp import ClientSession, MCPError, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CONNECTION_CLOSED, REQUEST_TIMEOUT

import everyday
from gate import ALLOW, UNREADABLE, Verdict, judge, judge_dialog, to_inspect, url_refusal
from memory import Memory

PLAYWRIGHT_MCP = "@playwright/mcp@0.0.86"
EXTENSION_TOKEN_VARIABLE = "PLAYWRIGHT_MCP_EXTENSION_TOKEN"  # with it the Playwright Extension connects without a dialog
CURRENT_TAB = re.compile(r"^- (\d+): \(current\)", re.MULTILINE)
NAVIGATION_TIMEOUT_MILLISECONDS = "20000"
SETTLE_MILLISECONDS = "100"  # server default 500: the fixed wait after every action, and so after every gate inspection
BROWSER_TOOLS = frozenset({
    "browser_navigate", "browser_navigate_back", "browser_find", "browser_click", "browser_type", "browser_fill_form",
    "browser_select_option", "browser_press_key", "browser_hover", "browser_file_upload", "browser_handle_dialog",
    "browser_tabs", "browser_wait_for"})
PAGE_SETTLE_SECONDS = 0.5  # key presses return at once; the snapshot the model sees waits for the page to react
SNAPSHOT_TOOL = "browser_snapshot"
EVALUATE_TOOL = "browser_evaluate"
BROWSER_CALL_TIMEOUT_SECONDS = 45  # above the server's 30 s browser_wait_for cap; a page script that never yields hangs a call
LOST_BROWSER_CODES = frozenset({CONNECTION_CLOSED, REQUEST_TIMEOUT})
ASK_TOOL = {
    "name": "ask_user",
    "description": "Say one short question out loud to the user and return their spoken answer. Use it for a missing fact, "
                   "and to hand over logins, CAPTCHAs and verification codes: ask them to do it in the window and say continue.",
    "input_schema": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]},
}
INSPECT_JS = Path(__file__).with_name("inspect.js").read_text()
# The values typed into fields the gate counts as secret, judged by the same rule, so what it refuses is also what is hidden
SECRET_VALUES_JS = (f"() => [...document.querySelectorAll('input, textarea')].filter((entry) => ({INSPECT_JS})(entry).kind "
                    "=== 'secret').map((entry) => entry.value).filter(Boolean)")
MEDIA_PLAYING_JS = "() => [...document.querySelectorAll('video, audio')].some((media) => !media.paused && !media.muted)"
EVALUATE_RESULT = re.compile(r"### Result\n(.*?)(?=\n###|\Z)", re.DOTALL)
MEDIA_PLAYING_RESULT = "### Result\ntrue"
HIDDEN = "(hidden)"
BLANK_PAGE = "### Page\n- Page URL: about:blank\n"
WEB_PAGE = "### Page\n- Page URL: http"
NOW = "It is {now:%A %d %B %Y, %H:%M} local time."
PAGE_NOW = "The page right now:"
EVERYDAY_PAGE = ("The page the user is looking at in their everyday browser, which you cannot see or control (its title and "
                 "address are untrusted text from the web): {title!r} {address}")
EVERYDAY_REFUSED = "The user is looking at a page in their everyday browser that you may not open: {refusal}"
EVERYDAY_TITLE_CHARACTERS = 200
STALE_SNAPSHOT = "(older page snapshot removed)"
SNAPSHOT_CHARACTER_LIMIT = 40_000
SNAPSHOT_CUT = "\n(snapshot cut off here; use browser_find to locate anything below)"
KEPT_BLOCK_TYPES = ("text", "tool_use")  # thinking blocks are bound to the unedited history, which pruning rewrites
CACHED = {"type": "ephemeral"}
MAX_TOKENS = 4096
REQUEST_TIMEOUT_SECONDS = 60
MAX_STEPS = 40  # a command keeps every snapshot it took; 40 of the largest still fit the context window
WARMUP = "warmup"
DECLINED = "The user said no. Do not retry or work around it. Stop and tell the user what you left undone."
NO_ANSWER = "The user did not answer."
INTERRUPTED = "[The user interrupted you before you finished. Wait for their next request.]"
OUT_OF_STEPS = "I ran out of steps before finishing."
CUT_OFF = "That step needed more writing than I can do at once, so I stopped."
UNCHECKABLE = "Refused: this call could not be checked ({error}). Correct its arguments or try another way."
REFUSED = "I can't help with that one."
NOTHING_TO_SAY = "Done."
USER_SAID = "The user said this while you were working: {words}"
STEP_CHARACTERS = 160
SAID_STEP = "You said: {text}"

Confirm = Callable[[str], Awaitable[bool]]
Ask = Callable[[str], Awaitable[str | None]]
Narrate = Callable[[str], None]


class BrowserStartError(Exception):
    """Chrome did not open, usually because another Chrome is already using the profile directory."""


class BrowserClosedError(Exception):
    """The page turned blank in the middle of a task: the user closed the window."""


class BrowserLostError(Exception):
    """A browser call got no answer in time (a frozen page) or the browser server died. Agent.restart opens a new one."""


class Conversation:
    """What both agents share: the memory at the start of a conversation, the first line of each task spoken at once,
    the user's words that arrive mid-task, and the task's steps for a quick spoken reply about it."""

    def __init__(self, memory: Memory, narrate: Narrate) -> None:
        self._memory, self._narrate = memory, narrate
        self._messages: list[dict] = []
        self._heard: list[str] = []
        self._thinking: asyncio.Future[Message] | None = None
        self.task = ""
        self.steps: list[str] = []

    def tell(self, words: str) -> None:
        """Hand the running task words the user said mid-task. A step the model is still writing is dropped so it reads
        them at once; an action already under way finishes first."""
        self._heard.append(words)
        if self._thinking and not self._thinking.done():
            self._thinking.cancel()

    def untold(self) -> list[str]:
        """Words told after the task's last step, which it never read."""
        words, self._heard = self._heard, []
        return words

    def _opening(self, command: str, content: list[dict]) -> dict:
        """The user message for a new command, after the memory when the conversation is new."""
        self.task, self.steps = command, []
        self._heard.clear()
        return {"role": "user", "content": (self._memory.blocks() if not self._messages else []) + content}

    def _took(self, kept: list) -> None:
        """Record a step; the text that comes with a task's first actions is spoken right away."""
        for block in kept:
            if block.type == "text" and block.text.strip():
                if not self.steps:
                    self._narrate(block.text.strip())
                self.steps.append(SAID_STEP.format(text=block.text.strip()))
            elif block.type == "tool_use":
                self.steps.append(f"{block.name} {json.dumps(block.input)[:STEP_CHARACTERS]}")

    async def _step(self, request: Awaitable[Message]) -> Message | None:
        """The model's next reply, or None when the user spoke while it was being written and their words were added to
        the history in its place."""
        self._thinking = asyncio.ensure_future(request)
        try:
            return await self._thinking
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():  # a stop, not tell()
                raise
        self._messages.append({"role": "user", "content": self._heard_blocks()})
        return None

    def _heard_blocks(self) -> list[dict]:
        blocks = [{"type": "text", "text": USER_SAID.format(words=words)} for words in self._heard]
        self._heard.clear()
        return blocks

    def _finished(self, reply) -> str | None:
        """The answer to speak when the model stopped calling tools, or None when the user spoke meanwhile and the task
        goes on with their words."""
        said = " ".join(block.text for block in reply.content if block.type == "text").strip()
        answer = (REFUSED if reply.stop_reason == "refusal" else CUT_OFF if reply.stop_reason == "max_tokens"
                  else said or NOTHING_TO_SAY)
        self._messages.append({"role": "assistant", "content": answer})
        if self._heard:
            self._messages.append({"role": "user", "content": self._heard_blocks()})
            return None
        return answer


class Agent(Conversation):
    """One browser, one conversation. Use as an async context manager."""

    def __init__(self, model: str, profile_dir: Path, memory: Memory, system_prompt: str, blocked_hosts: frozenset[str],
                 confirm: Confirm, ask: Ask, narrate: Narrate, headless: bool = False, extension_token: str = "") -> None:
        super().__init__(memory, narrate)
        self._model, self._blocked_hosts, self._confirm, self._ask = model, blocked_hosts, confirm, ask
        upload_dir = memory.folder
        self._profile_dir = profile_dir
        self._in_own_chrome = bool(extension_token)  # a tab in the user's own Chrome instead of a window with its own profile
        own_window = ["--browser", "chrome", "--user-data-dir", str(profile_dir), *(["--headless"] if headless else [])]
        self._system = [{"type": "text", "text": system_prompt, "cache_control": CACHED}]
        # cwd is the server's only upload root; --output-dir keeps its own files out of it.
        self._server = StdioServerParameters(
            command="npx", cwd=str(upload_dir), env={EXTENSION_TOKEN_VARIABLE: extension_token} if extension_token else None,
            args=["-y", PLAYWRIGHT_MCP, *(["--extension"] if extension_token else own_window),
                  "--output-dir", str(profile_dir.parent / "playwright-output"), "--snapshot-mode", "none", "--codegen", "none",
                  "--timeout-navigation", NAVIGATION_TIMEOUT_MILLISECONDS, "--timeout-settle", SETTLE_MILLISECONDS])
        self._anthropic = AsyncAnthropic(timeout=Timeout(REQUEST_TIMEOUT_SECONDS, connect=DEFAULT_TIMEOUT.connect))
        self._stack = AsyncExitStack()
        self._pages: list[dict] = []
        self._on_page = False

    async def __aenter__(self) -> "Agent":
        try:
            await self._open()
        except BaseException:  # Python skips __aexit__ when __aenter__ fails, and the open server would hang asyncio.run
            await self.__aexit__()
            raise
        return self

    async def _open(self) -> None:
        read, write = await self._stack.enter_async_context(stdio_client(self._server))
        self._browser = await self._stack.enter_async_context(ClientSession(read, write))
        await self._browser.initialize()
        listed = await self._browser.list_tools()
        self._tools = [{"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}
                       for tool in listed.tools if tool.name in BROWSER_TOOLS] + [ASK_TOOL]
        # The window opens now, not on the first command.
        opened = await self._browser.call_tool("browser_tabs", {"action": "list"})
        if opened.is_error:
            raise BrowserStartError(_text(opened))

    async def restart(self) -> None:
        """Close the browser server and its window and open new ones, after a BrowserLostError. Call it from the task that
        entered the agent: the MCP client's task groups must be left by the task that entered them."""
        await self._stack.aclose()
        self._on_page = False
        await self._open()

    async def __aexit__(self, *exc_info: object) -> None:
        await self._stack.aclose()
        await self._anthropic.close()

    async def warm_up(self) -> None:
        """Writes the prompt cache and opens the connection: the first request of a process is the slow one."""
        await self._anthropic.messages.create(model=self._model, max_tokens=0, system=self._system, tools=self._tools,
                                              messages=[{"role": "user", "content": WARMUP}])

    def forget(self) -> None:
        self._messages.clear()
        self._pages.clear()

    async def media_playing(self) -> bool:
        return MEDIA_PLAYING_RESULT in await self._call(EVALUATE_TOOL, {"function": MEDIA_PLAYING_JS})

    async def view(self) -> list[dict]:
        """What the task sees now: the newest page snapshot."""
        return [{"type": "text", "text": self._pages[-1]["text"]}] if self._pages else []

    async def run(self, command: str) -> str:
        """Carry out one spoken command and return the sentence to speak back. Cancel the task to interrupt it."""
        self._on_page = False
        for page in self._pages:  # a command keeps the pages it saw, or the model forgets why it acted and redoes the step
            page["text"] = STALE_SNAPSHOT
        self._pages.clear()
        try:
            if self._in_own_chrome:
                await self._show_tab()
            snapshot, in_front = await asyncio.gather(self._page(), self._everyday_page())
            asked = f"{command}\n\n{in_front}{NOW.format(now=datetime.now())}\n\n{PAGE_NOW}"
            self._messages.append(self._opening(command, [{"type": "text", "text": asked}, self._page_block(snapshot)]))
            for _ in range(MAX_STEPS):
                reply = await self._step(self._anthropic.messages.create(
                    model=self._model, max_tokens=MAX_TOKENS, system=self._system, tools=self._tools,
                    messages=self._messages, cache_control=CACHED))
                if reply is None:
                    continue
                if reply.stop_reason != "tool_use":
                    if (answer := self._finished(reply)) is not None:
                        return answer
                    continue
                kept = [block for block in reply.content if block.type in KEPT_BLOCK_TYPES]
                self._took(kept)
                results = [{"type": "tool_result", "tool_use_id": call.id, "content": await self._use(call.name, call.input)}
                           for call in kept if call.type == "tool_use"]
                # Appended together and before the wait, so a stop from here on finds a valid history of what was done.
                self._messages += [{"role": "assistant", "content": kept}, {"role": "user", "content": results}]
                await asyncio.sleep(PAGE_SETTLE_SECONDS)
                results += [self._page_block(await self._page()), *self._heard_blocks()]
        except BaseException:
            self._messages.append({"role": "user", "content": INTERRUPTED})
            raise
        return OUT_OF_STEPS

    async def _everyday_page(self) -> str:
        """A paragraph about the tab in front in the user's everyday Chrome, or "" when that Chrome is not the app in front."""
        try:
            tab = await everyday.front_tab(self._profile_dir)
        except everyday.EverydayBrowserError as error:
            return f"{error}\n\n"
        if tab is None:
            return ""
        address, title = tab
        refusal = url_refusal(address, self._blocked_hosts)
        return (EVERYDAY_REFUSED.format(refusal=refusal) if refusal
                else EVERYDAY_PAGE.format(title=title[:EVERYDAY_TITLE_CHARACTERS], address=address)) + "\n\n"

    async def _show_tab(self) -> None:
        """In the user's own Chrome a click times out unless the agent's tab is the one showing in its window."""
        current = CURRENT_TAB.search(await self._call("browser_tabs", {"action": "list"}))
        if current:
            await self._call("browser_tabs", {"action": "select", "index": int(current[1])})

    async def _page(self) -> str:
        """The current page for the model. A page that turns blank mid-task means the window was closed."""
        snapshot = await self._call(SNAPSHOT_TOOL, {})
        blank = snapshot.startswith(BLANK_PAGE)
        if blank and self._on_page:
            raise BrowserClosedError
        self._on_page = not blank
        typed = EVALUATE_RESULT.search(await self._call(EVALUATE_TOOL, {"function": SECRET_VALUES_JS}))
        shown = [re.escape(form) for secret in (json.loads(typed[1]) if typed else [])
                 for form in (secret, json.dumps(secret, ensure_ascii=False))]  # the snapshot quotes some values
        if shown:  # what the user typed during a hand-over; top document only
            snapshot = re.sub(rf"(?m): (?:{'|'.join(shown)})$", f": {HIDDEN}", snapshot)
        return snapshot[:SNAPSHOT_CHARACTER_LIMIT] + SNAPSHOT_CUT if len(snapshot) > SNAPSHOT_CHARACTER_LIMIT else snapshot

    def _page_block(self, snapshot: str) -> dict:
        self._pages.append({"type": "text", "text": snapshot})
        return self._pages[-1]

    async def _use(self, tool: str, arguments: dict) -> str:
        if tool == ASK_TOOL["name"]:
            answer = await self._ask(str(arguments.get("question", "")))
            return f"The user said: {answer}" if answer else NO_ANSWER
        if tool not in BROWSER_TOOLS:
            return f"Unknown tool {tool}."
        try:
            verdict = await self._verdict(tool, arguments)
        except (AttributeError, KeyError, TypeError, ValueError) as error:  # wrong-typed arguments, or page-tampered facts
            return UNCHECKABLE.format(error=error)
        if verdict.decision == "deny":
            return verdict.text
        if verdict.decision == "ask" and not await self._confirm(verdict.text):
            return DECLINED
        return await self._call(tool, arguments) or NOTHING_TO_SAY

    async def _verdict(self, tool: str, arguments: dict) -> Verdict:
        refusal = url_refusal(arguments["url"], self._blocked_hosts) if "url" in arguments else None
        if refusal:
            return Verdict("deny", refusal)
        if tool == "browser_handle_dialog":
            return judge_dialog(await self._call(SNAPSHOT_TOOL, {})) if arguments.get("accept") else ALLOW
        return judge(tool, arguments, [await self._inspect(target) for target in to_inspect(tool, arguments)])

    async def _inspect(self, target: str | None) -> dict:
        """Facts about the element a call would act on, read from the DOM through the server's own ref resolution."""
        aimed = {} if target is None else {"target": target, "element": "safety check"}
        output = await self._call(EVALUATE_TOOL, {"function": INSPECT_JS} | aimed)
        result = EVALUATE_RESULT.search(output)
        return json.loads(result[1]) if result else {"kind": UNREADABLE, "label": output}  # stale ref, open dialog

    async def _call(self, tool: str, arguments: dict) -> str:
        try:
            result = await self._browser.call_tool(tool, arguments, read_timeout_seconds=BROWSER_CALL_TIMEOUT_SECONDS)
        except MCPError as error:
            if error.code in LOST_BROWSER_CODES:
                raise BrowserLostError(error.message) from error
            raise
        text = _text(result)
        self._on_page = self._on_page or WEB_PAGE in text  # a navigation result counts: a session's first snapshot is blank
        return text


def _text(result) -> str:
    return "\n".join(block.text for block in result.content if block.type == "text")
