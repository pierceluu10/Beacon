"""Claude driving a visible Chrome through the Playwright MCP server. Every browser call passes the gate first."""
import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from pathlib import Path

from anthropic import AsyncAnthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from gate import ALLOW, UNREADABLE, Verdict, judge, judge_dialog, to_inspect, url_refusal

PLAYWRIGHT_MCP = "@playwright/mcp@0.0.86"
NAVIGATION_TIMEOUT_MILLISECONDS = "20000"
SETTLE_MILLISECONDS = "100"  # server default 500: the fixed wait after every action, and so after every gate inspection
BROWSER_TOOLS = frozenset({
    "browser_navigate", "browser_navigate_back", "browser_find", "browser_click", "browser_type", "browser_fill_form",
    "browser_select_option", "browser_press_key", "browser_hover", "browser_file_upload", "browser_handle_dialog",
    "browser_tabs", "browser_wait_for"})
PAGE_SETTLE_SECONDS = 0.5  # key presses return at once; the snapshot the model sees waits for the page to react
SNAPSHOT_TOOL = "browser_snapshot"
EVALUATE_TOOL = "browser_evaluate"
ASK_TOOL = {
    "name": "ask_user",
    "description": "Say one short question out loud to the user and return their spoken answer. Use it for a missing fact, "
                   "and to hand over logins, CAPTCHAs and verification codes: ask them to do it in the window and say continue.",
    "input_schema": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]},
}
INSPECT_JS = Path(__file__).with_name("inspect.js").read_text()
MEDIA_PLAYING_JS = "() => [...document.querySelectorAll('video, audio')].some((media) => !media.paused && !media.muted)"
EVALUATE_RESULT = re.compile(r"### Result\n(.*?)(?=\n###|\Z)", re.DOTALL)
MEDIA_PLAYING_RESULT = "### Result\ntrue"
BLANK_PAGE = "### Page\n- Page URL: about:blank\n"
PAGE_NOW = "The page right now:"
STALE_SNAPSHOT = "(older page snapshot removed)"
SNAPSHOT_CHARACTER_LIMIT = 40_000
SNAPSHOT_CUT = "\n(snapshot cut off here; use browser_find to locate anything below)"
KEPT_BLOCK_TYPES = ("text", "tool_use")  # thinking blocks are bound to the unedited history, which pruning rewrites
MAX_TOKENS = 4096
REQUEST_TIMEOUT_SECONDS = 60
MAX_STEPS = 60
WARMUP = "warmup"
DECLINED = "The user said no. Do not retry or work around it. Stop and tell the user what you left undone."
NO_ANSWER = "The user did not answer."
INTERRUPTED = "[The user interrupted you before you finished. Wait for their next request.]"
OUT_OF_STEPS = "I ran out of steps before finishing."
REFUSED = "I can't help with that one."
NOTHING_TO_SAY = "Done."

Confirm = Callable[[str], Awaitable[bool]]
Ask = Callable[[str], Awaitable[str | None]]


class BrowserStartError(Exception):
    """Chrome did not open, usually because another Chrome is already using the profile directory."""


class BrowserClosedError(Exception):
    """The page turned blank in the middle of a task: the user closed the window."""


class Agent:
    """One browser, one conversation. Use as an async context manager."""

    def __init__(self, model: str, profile_dir: Path, upload_dir: Path, system_prompt: str, blocked_hosts: frozenset[str],
                 confirm: Confirm, ask: Ask, headless: bool = False) -> None:
        self._model, self._blocked_hosts, self._confirm, self._ask = model, blocked_hosts, confirm, ask
        self._system = [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]
        # cwd is the server's only upload root; --output-dir keeps its own files out of it.
        self._server = StdioServerParameters(command="npx", cwd=str(upload_dir), args=[
            "-y", PLAYWRIGHT_MCP, "--browser", "chrome", "--user-data-dir", str(profile_dir),
            "--output-dir", str(profile_dir.parent / "playwright-output"), "--snapshot-mode", "none", "--codegen", "none",
            "--timeout-navigation", NAVIGATION_TIMEOUT_MILLISECONDS, "--timeout-settle", SETTLE_MILLISECONDS,
            *(["--headless"] if headless else [])])
        self._anthropic = AsyncAnthropic(timeout=REQUEST_TIMEOUT_SECONDS)
        self._stack = AsyncExitStack()
        self._messages: list[dict] = []
        self._latest_page: dict | None = None
        self._on_page = False

    async def __aenter__(self) -> "Agent":
        read, write = await self._stack.enter_async_context(stdio_client(self._server))
        self._browser = await self._stack.enter_async_context(ClientSession(read, write))
        await self._browser.initialize()
        listed = await self._browser.list_tools()
        self._tools = [{"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}
                       for tool in listed.tools if tool.name in BROWSER_TOOLS] + [ASK_TOOL]
        opened = await self._browser.call_tool("browser_tabs", {"action": "list"})  # the window opens now, not on the first command
        if opened.is_error:
            raise BrowserStartError(_text(opened))
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self._stack.aclose()
        await self._anthropic.close()

    async def warm_up(self) -> None:
        """Writes the prompt cache and opens the connection: the first request of a process is the slow one."""
        await self._anthropic.messages.create(model=self._model, max_tokens=0, system=self._system, tools=self._tools,
                                              messages=[{"role": "user", "content": WARMUP}])

    def forget(self) -> None:
        self._messages.clear()
        self._latest_page = None

    async def media_playing(self) -> bool:
        return MEDIA_PLAYING_RESULT in await self._call(EVALUATE_TOOL, {"function": MEDIA_PLAYING_JS})

    async def run(self, command: str) -> str:
        """Carry out one spoken command and return the sentence to speak back. Cancel the task to interrupt it."""
        self._on_page = False
        try:
            self._messages.append({"role": "user", "content": [{"type": "text", "text": f"{command}\n\n{PAGE_NOW}"},
                                                               self._page_block(await self._page())]})
            for _ in range(MAX_STEPS):
                reply = await self._anthropic.messages.create(model=self._model, max_tokens=MAX_TOKENS, system=self._system,
                                                              tools=self._tools, messages=self._messages)
                if reply.stop_reason != "tool_use":
                    said = " ".join(block.text for block in reply.content if block.type == "text").strip()
                    answer = REFUSED if reply.stop_reason == "refusal" else said or NOTHING_TO_SAY
                    self._messages.append({"role": "assistant", "content": answer})
                    return answer
                kept = [block for block in reply.content if block.type in KEPT_BLOCK_TYPES]
                results = [{"type": "tool_result", "tool_use_id": call.id, "content": await self._use(call.name, call.input)}
                           for call in kept if call.type == "tool_use"]
                await asyncio.sleep(PAGE_SETTLE_SECONDS)
                # Appended together, so the history is valid whenever this task is cancelled.
                self._messages += [{"role": "assistant", "content": kept},
                                   {"role": "user", "content": [*results, self._page_block(await self._page())]}]
        except BaseException:
            self._messages.append({"role": "user", "content": INTERRUPTED})
            raise
        return OUT_OF_STEPS

    async def _page(self) -> str:
        """The current page for the model. A page that turns blank mid-task means the window was closed."""
        snapshot = await self._call(SNAPSHOT_TOOL, {})
        blank = snapshot.startswith(BLANK_PAGE)
        if blank and self._on_page:
            raise BrowserClosedError
        self._on_page = not blank
        return snapshot[:SNAPSHOT_CHARACTER_LIMIT] + SNAPSHOT_CUT if len(snapshot) > SNAPSHOT_CHARACTER_LIMIT else snapshot

    def _page_block(self, snapshot: str) -> dict:
        if self._latest_page:
            self._latest_page["text"] = STALE_SNAPSHOT
        self._latest_page = {"type": "text", "text": snapshot}
        return self._latest_page

    async def _use(self, tool: str, arguments: dict) -> str:
        if tool == ASK_TOOL["name"]:
            answer = await self._ask(str(arguments.get("question", "")))
            return f"The user said: {answer}" if answer else NO_ANSWER
        if tool not in BROWSER_TOOLS:
            return f"Unknown tool {tool}."
        verdict = await self._verdict(tool, arguments)
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
        return _text(await self._browser.call_tool(tool, arguments))


def _text(result) -> str:
    return "\n".join(block.text for block in result.content if block.type == "text")
