"""No API, no human: the real Playwright MCP server (headless) against tests/site/form.html, calls through Agent._use."""
import asyncio, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from agent import Agent, BrowserClosedError

F = Path(sys.argv[1]); FORM = "http://localhost:8771/form.html"
asked: list[str] = []; answer = [False]
async def confirm(question: str) -> bool:
    asked.append(question); return answer[0]
async def ask(question: str) -> str | None: return "continue"

async def main() -> None:
    async with Agent("claude-opus-5-5", F / "profile", F / "uploads", "test", frozenset({"bank.example"}), confirm, ask, headless=True) as agent:
        async def go() -> dict[str, str]:
            await agent._call("browser_navigate", {"url": FORM})
            snap = await agent._call("browser_snapshot", {})
            refs = {}
            for role, name, ref in re.findall(r'- (\w+) "([^"]*)" \[ref=(\w+)\]', snap): refs.setdefault(f"{role}:{name}", ref)
            refs["frames:send"] = re.findall(r'button "Send message" \[ref=(\w+)\]', snap)
            refs["frames:field"] = re.findall(r'textbox "Frame message" \[ref=(\w+)\]', snap)
            return refs
        async def log() -> str:
            await asyncio.sleep(0.3)
            return re.search(r'paragraph \[ref=\w+\]: "?(LOG:[^"\n]*)', await agent._call("browser_snapshot", {}))[1]
        failures = []
        async def expect(name, tool, arguments, asks: bool, said_yes: bool = False, contains: str = "", log_has: str = "", log_lacks: str = ""):
            asked.clear(); answer[0] = said_yes
            out = await agent._use(tool, arguments)
            page = await log() if (log_has or log_lacks) else ""
            ok = (bool(asked) == asks) and (contains in out) and (log_has in page) and (not log_lacks or log_lacks not in page)
            print(f"{'ok  ' if ok else 'FAIL'} {name:46s} asked={asked[:1]} out={out[:90]!r} {page}")
            if not ok: failures.append(name)
        r = await go()
        click = lambda key, **kw: ("browser_click", {"target": r[key], "element": "x"} | kw)
        # harmless: no question
        await expect("click into a field", *click("textbox:Full name"), asks=False)
        await expect("fill fields", "browser_fill_form", {"fields": [{"name": "Full name", "type": "textbox", "target": r["textbox:Full name"], "element": "n", "value": "Pierce Luu"}, {"name": "Email", "type": "textbox", "target": r["textbox:Email"], "element": "e", "value": "p@example.com"}]}, asks=False)
        await expect("tick agree checkbox", *click("checkbox:I agree to the terms"), asks=False)
        await expect("search with Enter", "browser_type", {"target": r["textbox:Search jobs"], "text": "x", "submit": True, "element": "s"}, asks=False)
        r = await go()
        await expect("long link title with delete/remove", *click("link:How to delete your account and remove posts forever"), asks=False)
        r = await go()
        await expect("same-page link", *click("link:Next page"), asks=False, log_has="NEXT")
        await expect("multi-paragraph fill is not Enter", "browser_type", {"target": r["textbox:Cover letter"], "text": "Dear team,\nhello", "element": "c"}, asks=False)
        await expect("Space in a text field", "browser_press_key", {"key": "Space"}, asks=False)
        # irreversible: asked once, a no leaves the page untouched
        await expect("Submit application: no", *click("button:Submit application"), asks=True, contains="The user said no", log_lacks="SUBMITTED")
        await expect("Apply as a form submit button: no", *click("button:Apply"), asks=True, log_lacks="APPLIED")
        await expect("aria-label Send icon: no", *click("button:Send"), asks=True, log_lacks="SENT")
        await expect("title-only Pay now: no", *click("button:Pay now"), asks=True, log_lacks="PAID")
        await expect("div role=button Place order: no", *click("button:Place order"), asks=True, log_lacks="ORDERED")
        await expect("short link Unsubscribe: no", *click("link:Unsubscribe"), asks=True)
        await expect("a role=button Remove: no", *click("button:Remove"), asks=True, log_lacks="REMOVED")
        await expect("iframe Send message: no", "browser_click", {"target": r["frames:send"][0], "element": "x"}, asks=True)
        await expect("cross-origin iframe Send message: no", "browser_click", {"target": r["frames:send"][1], "element": "x"}, asks=True)
        await expect("typed submit in a form field: no", "browser_type", {"target": r["textbox:Full name"], "text": "P", "submit": True, "element": "n"}, asks=True, log_lacks="SUBMITTED")
        await expect("slow-typed newline in formless field: no", "browser_type", {"target": r["textbox:Comment"], "text": "hi\n", "slowly": True, "element": "c"}, asks=True, log_lacks="COMMENT")
        await agent._call("browser_click", {"target": r["textbox:Comment"], "element": "c"})
        await expect("Enter in formless field: no", "browser_press_key", {"key": "Enter"}, asks=True, log_lacks="COMMENT")
        await expect("Control+Enter chord: no", "browser_press_key", {"key": "Control+Enter"}, asks=True, log_lacks="COMMENT")
        await agent._call("browser_click", {"target": r["generic:Message"], "element": "m"})
        await expect("Enter in contenteditable chat box: no", "browser_press_key", {"key": "Enter"}, asks=True, log_lacks="CHAT")
        await agent._call("browser_click", {"target": r["frames:field"][1], "element": "m"})
        await expect("Enter inside cross-origin iframe: no", "browser_press_key", {"key": "Enter"}, asks=True)
        await agent._call("browser_click", {"target": r["textbox:Search jobs"], "element": "s"}); await agent._call("browser_press_key", {"key": "Tab"})
        await expect("Space on focused Search button", "browser_press_key", {"key": "Space"}, asks=False)
        await asyncio.sleep(0.6); r = await go()
        await agent._call("browser_fill_form", {"fields": [{"name": "Full name", "type": "textbox", "target": r["textbox:Full name"], "element": "n", "value": "Pierce Luu"}]})
        # refusals without a question
        await expect("type into password", "browser_type", {"target": r["textbox:Password"], "text": "hunter2", "element": "p"}, asks=False, contains="password, card")
        await expect("fill card via fill_form", "browser_fill_form", {"fields": [{"name": "Card", "type": "textbox", "target": r["textbox:Card"], "element": "c", "value": "4111"}]}, asks=False, contains="password, card")
        await agent._call("browser_click", {"target": r["textbox:Code"], "element": "c"})
        await expect("press a key in one-time-code field", "browser_press_key", {"key": "1"}, asks=False, contains="password, card")
        await expect("stale ref", "browser_click", {"target": "e999", "element": "x"}, asks=False, contains="not found")
        await expect("navigate file://", "browser_navigate", {"url": "file:///etc/hosts"}, asks=False, contains="Only http")
        await expect("navigate loopback", "browser_navigate", {"url": "http://127.0.0.1:8771/form.html"}, asks=False, contains="loopback")
        await expect("navigate blocked host", "browser_tabs", {"action": "new", "url": "https://www.bank.example/"}, asks=False, contains="blocked")
        await expect("unknown tool", "browser_evaluate", {"function": "() => 1"}, asks=False, contains="Unknown tool")
        # upload: outside the upload folder is refused by the server; inside works; the submit question names the file
        await agent._call("browser_click", {"target": r["button:Resume"], "element": "r"})
        await expect("upload outside upload folder", "browser_file_upload", {"paths": [str(F / "work/other.txt")]}, asks=False, contains="File access denied")
        await expect("upload resume", "browser_file_upload", {"paths": [str(F / "uploads/resume.pdf")]}, asks=False)
        await expect("Submit application: yes", *click("button:Submit application"), asks=True, said_yes=True, log_has="SUBMITTED:Pierce Luu:resume.pdf")
        print("   question was:", asked[0])
        # dialogs
        await agent._call("browser_click", {"target": r["button:Close account"], "element": "x"})
        await expect("accept confirm(): no", "browser_handle_dialog", {"accept": True}, asks=True, contains="The user said no")
        print("   question was:", asked[0])
        await expect("dismiss confirm()", "browser_handle_dialog", {"accept": False}, asks=False, log_lacks="DELETED")
        await agent._call("browser_click", {"target": r["button:Save draft"], "element": "x"})
        await expect("accept alert()", "browser_handle_dialog", {"accept": True}, asks=False)
        # media and the closed window
        print("media playing:", await agent.media_playing())
        agent._on_page = False; await agent._page()
        await agent._call("browser_tabs", {"action": "close"})
        try:
            await agent._page(); failures.append("closed window not detected")
        except BrowserClosedError:
            print("ok   closed window raises BrowserClosedError")
        print("FAILURES:", failures or "none")
asyncio.run(main())
