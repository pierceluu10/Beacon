"""The gate on a real page: the real Playwright MCP server drives headless Chrome on tests/site/form.html and every call
goes through Agent._use, with a scripted user in place of the microphone. No Anthropic API request is made."""
import asyncio
import json
import operator
import re
from collections.abc import Awaitable, Iterator
from pathlib import Path
from typing import NamedTuple

import pytest

from agent import (DECLINED, EVALUATE_RESULT, EVALUATE_TOOL, NO_ANSWER, NOTHING_TO_SAY, PAGE_SETTLE_SECONDS, Agent,
                   BrowserClosedError)
from conftest import scratch_home
from gate import FILE_REFUSAL, SECRET_REFUSAL, SPOKEN_LABEL_CHARACTERS

NEVER_SENT = "never sent: these tests make no API request"
BLOCKED_HOST = "bank.example"
CALL_TIMEOUT_SECONDS = 30
FORM = "/form.html"
SERVER_ERROR = "### Error"
PAGE_REPORT = "### Page\n- Page URL: {site}"  # how the server's result starts when a call left the page at a new address
PAGE_ADDRESS = re.compile(r"^- Page URL: (.*)$", re.MULTILINE)
PAGE_LOG = re.compile(r'paragraph \[ref=\w+\]: "?(LOG:[^"\n]*)')
FRAME_SENT = "FRAME-SENT"
QUESTION = "Ready to {action}. Say yes to go ahead, or no to cancel."
DIALOG_QUESTION = "The page asks: Really delete your account. Say yes to accept, or no to cancel."
SPOKEN_QUESTION = "What is your phone number?"
DIGEST_CARD = "Weekly digest" + " of new jobs" * 16 + " Subscribe"  # what the question keeps of the form page's longest label
ATTACHED_FILES_JS = "() => [...document.querySelector('input[type=file]').files].map((file) => file.name)"
START_AUDIO_JS = ("() => { const audio = new Audio(); document.body.append(audio);"
                  " audio.srcObject = new AudioContext().createMediaStreamDestination().stream; return audio.play(); }")


class Element(NamedTuple):
    """An element as the page snapshot names it; Browser swaps it for the element's ref when the call is made."""
    line: str
    occurrence: int = 0  # the embedded form appears twice: same-origin first, cross-origin second


class Page(NamedTuple):
    path: str  # the address after the site's base
    log: str | None  # the form's log paragraph, which every handler on the page appends to; None once the form is gone
    frames_sent: int  # how many of the two embedded forms were submitted


UNTOUCHED = Page(FORM, "LOG:", 0)


class Outcome(NamedTuple):
    asked: list[str]  # every question the user heard
    returned: str  # the tool result the model reads
    page: Page


class Case(NamedTuple):
    """One call through the gate on a fresh form page, with a user who answers no."""
    name: str
    tool: str
    arguments: dict
    before: tuple[tuple[str, dict], ...] = ()  # ungated calls that set the page up, such as a click to move keyboard focus
    question: str = ""  # what the one spoken question must offer to do
    returns: str = NOTHING_TO_SAY  # how the tool result must start
    page: Page = UNTOUCHED


FULL_NAME = Element('textbox "Full name"')
COVER_LETTER = Element('textbox "Cover letter"')
COMMENT = Element('textbox "Comment"')
RESUME = Element('button "Resume"')
SUBMIT = Element('button "Submit application"')
CLOSE_ACCOUNT = Element('button "Close account"')
AGREE = Element('checkbox "I agree to the terms"')


def click(element: Element) -> tuple[str, dict]:
    return "browser_click", {"target": element, "element": element.line}


def type_into(element: Element, text: str, **options: bool) -> tuple[str, dict]:
    return "browser_type", {"target": element, "element": element.line, "text": text, **options}


def fill(values: dict[Element, str]) -> tuple[str, dict]:
    return "browser_fill_form", {"fields": [
        {"name": element.line, "type": "textbox", "target": element, "element": element.line, "value": value}
        for element, value in values.items()]}


def press(key: str) -> tuple[str, dict]:
    return "browser_press_key", {"key": key}


class Browser:
    """The module's one Agent on its own event loop, driven synchronously by the tests."""

    def __init__(self, site: str, home: Path) -> None:
        self.site, self.home = site, home
        self._asked: list[str] = []
        self._says_yes = False
        self._reply: str | None = None
        self._snapshot = ""
        self._loop = asyncio.new_event_loop()
        self._finished = asyncio.Event()

    def __enter__(self) -> "Browser":
        opened: asyncio.Future[Agent] = self._loop.create_future()
        self._session = self._loop.create_task(self._hold_open(opened))
        self._loop.run_until_complete(asyncio.wait({self._session, opened}, return_when=asyncio.FIRST_COMPLETED))
        if self._session.done():  # it only ends this early when Chrome or the server did not start
            self._loop.close()
            self._session.result()
        self.agent = opened.result()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._finished.set()
        self._loop.run_until_complete(self._session)
        self._loop.close()

    async def _hold_open(self, opened: asyncio.Future) -> None:
        # The MCP client's task groups must be left by the task that entered them, and each run_until_complete call is a
        # new task, so this one task keeps the agent open for the whole module.
        async with Agent(NEVER_SENT, self.home / "profile", self.home / "uploads", NEVER_SENT, frozenset({BLOCKED_HOST}),
                         self._confirm, self._ask, headless=True) as agent:
            opened.set_result(agent)
            await self._finished.wait()

    async def _confirm(self, question: str) -> bool:
        self._asked.append(question)
        return self._says_yes

    async def _ask(self, question: str) -> str | None:
        self._asked.append(question)
        return self._reply

    def run[Result](self, work: Awaitable[Result]) -> Result:
        return self._loop.run_until_complete(asyncio.wait_for(work, CALL_TIMEOUT_SECONDS))

    def call(self, tool: str, arguments: dict) -> str:
        """Straight to the server, past the gate: for setting a page up and reading it back."""
        result = self.run(self.agent._call(tool, self._aimed(arguments)))
        assert not result.startswith(SERVER_ERROR), f"{tool} {arguments} failed:\n{result}"
        return result

    def use(self, tool: str, arguments: dict, says_yes: bool = False, reply: str | None = None) -> tuple[list[str], str]:
        """One call through the gate, as the model makes it: the questions the user heard and the tool result.
        The user answers says_yes to a confirmation and reply to an ask_user question."""
        self._asked, self._says_yes, self._reply = [], says_yes, reply
        return self._asked, self.run(self.agent._use(tool, self._aimed(arguments)))

    def attempt(self, tool: str, arguments: dict, **answers: bool | str | None) -> Outcome:
        return Outcome(*self.use(tool, arguments, **answers), self.page())

    def open_form(self) -> None:
        """A fresh tab on the form. Closing the old tab also discards a dialog or file chooser a failed test left open,
        which would otherwise make the server skip the navigation."""
        self.call("browser_tabs", {"action": "close"})
        self.call("browser_navigate", {"url": f"{self.site}{FORM}"})  # the gate itself refuses loopback addresses
        self._snapshot = self.call("browser_snapshot", {})

    def page(self) -> Page:
        """What the page shows after the time the agent gives it to react before the model looks again."""
        self.run(asyncio.sleep(PAGE_SETTLE_SECONDS))
        self._snapshot = self.call("browser_snapshot", {})
        log = PAGE_LOG.search(self._snapshot)
        return Page(PAGE_ADDRESS.search(self._snapshot)[1].removeprefix(self.site), log and log[1],
                    self._snapshot.count(FRAME_SENT))

    def attached_files(self) -> list[str]:
        return json.loads(EVALUATE_RESULT.search(self.call(EVALUATE_TOOL, {"function": ATTACHED_FILES_JS}))[1])

    def _aimed(self, value: object) -> object:
        """A copy of a call's arguments with every Element replaced by its ref in the newest snapshot."""
        if isinstance(value, Element):
            refs = re.findall(rf'- {re.escape(value.line)}[^"\n]*\[ref=(\w+)\]', self._snapshot)
            assert len(refs) > value.occurrence, f"{value} is not in the snapshot:\n{self._snapshot}"
            return refs[value.occurrence]
        if isinstance(value, dict):
            return {key: self._aimed(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._aimed(item) for item in value]
        return value


@pytest.fixture(scope="module")
def browser(site: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Browser]:
    with scratch_home(tmp_path_factory.mktemp("agent")) as home, Browser(site, home) as browser:
        yield browser


def attempt_case(browser: Browser, case: Case) -> Outcome:
    browser.open_form()
    for tool, arguments in case.before:
        browser.call(tool, arguments)
    return browser.attempt(case.tool, case.arguments)


HARMLESS = [
    Case("click into a field", *click(FULL_NAME)),
    Case("fill two fields", *fill({FULL_NAME: "Pierce Luu", Element('textbox "Email"'): "p@example.com"})),
    Case("tick the agree checkbox", *click(AGREE)),
    Case("Enter in the search box", *type_into(Element('textbox "Search jobs"'), "x", submit=True),
         returns=PAGE_REPORT + "/s?q=x\n", page=Page("/s?q=x", None, 0)),
    Case("ten-word link title containing delete and remove",
         *click(Element('link "How to delete your account and remove posts forever"')),
         returns=PAGE_REPORT + "/other.html\n", page=Page("/other.html", None, 0)),
    Case("same-page link", *click(Element('link "Next page"')),
         returns=PAGE_REPORT + "/form.html#next\n", page=Page("/form.html#next", "LOG: NEXT", 0)),
    Case("multi-paragraph fill is not Enter", *type_into(COVER_LETTER, "Dear team,\nhello")),
    Case("Space in a text field", *press("Space"), before=(click(FULL_NAME),)),
    Case("Space on the focused Search button", *press("Space"),
         before=(click(Element('textbox "Search jobs"')), press("Tab")),
         returns=PAGE_REPORT, page=Page("/s?q=", None, 0)),  # a key press returns while the search is still loading
    Case("submit button of a form whose only sign of search is the box's name", *click(Element('button "Look up"')),
         returns=PAGE_REPORT + "/results?search_query=\n", page=Page("/results?search_query=", None, 0)),
    Case("slowly typed words in a field", *type_into(COMMENT, "hello there", slowly=True)),
    Case("dismiss a confirm() dialog", "browser_handle_dialog", {"accept": False}, before=(click(CLOSE_ACCOUNT),),
         returns=PAGE_REPORT + FORM + "\n"),
    Case("accept an alert()", "browser_handle_dialog", {"accept": True}, before=(click(Element('button "Save draft"')),),
         returns=PAGE_REPORT + FORM + "\n"),
]

IRREVERSIBLE = [
    Case("Submit application", *click(SUBMIT), question="press Submit application on localhost"),
    Case("Apply as a form's submit button", *click(Element('button "Apply"')), question="press Apply on localhost"),
    Case("icon named Send only by aria-label", *click(Element('button "Send"')), question="press Send on localhost"),
    Case("icon named Pay now only by title", *click(Element('button "Pay now"')), question="press Pay now on localhost"),
    Case("div with role=button, Place order", *click(Element('button "Place order"')),
         question="press Place order on localhost"),
    Case("one-word link, Unsubscribe", *click(Element('link "Unsubscribe"')), question="press Unsubscribe on localhost"),
    Case("link with role=button, Remove", *click(Element('button "Remove"')), question="press Remove on localhost"),
    Case("Send message in a same-origin iframe", *click(Element('button "Send message"', 0)),
         question="press Send message on localhost"),
    Case("Send message in a cross-origin iframe", *click(Element('button "Send message"', 1)),
         question="press Send message on 127.0.0.1"),
    Case("typing with submit in a form field", *type_into(FULL_NAME, "P", submit=True),
         question="press Enter in Full name on localhost"),
    Case("slowly typed newline in a field without a form", *type_into(COMMENT, "hi\n", slowly=True),
         question="press Enter in Comment on localhost"),
    Case("Enter in a field without a form", *press("Enter"), before=(click(COMMENT),),
         question="press Enter in Comment on localhost"),
    Case("Control+Enter chord", *press("Control+Enter"), before=(click(COMMENT),),
         question="press Enter in Comment on localhost"),
    Case("Enter in a contenteditable chat box", *press("Enter"), before=(click(Element('generic "Message"')),),
         question="press Enter in Message on localhost"),
    Case("Enter inside a cross-origin iframe", *press("Enter"), before=(click(Element('textbox "Frame message"', 1)),),
         question="press Enter in an embedded form on localhost"),
    Case("carriage return as the key", *press("\r"), before=(click(COMMENT),), question="press Enter in Comment on localhost"),
    Case("Enter written first in a chord", *press("Enter+Shift"), before=(click(FULL_NAME),),
         question="press Enter in Full name on localhost"),
    Case("Enter on the ticked agree checkbox", *press("Enter"), before=(click(AGREE),),
         question="press Enter in this form on localhost"),
    Case("typing with submit in a field named Research interests",
         *type_into(Element('textbox "Research interests"'), "x", submit=True),
         question="press Enter in Research interests on localhost"),
    Case("slowly typed space aimed at a button", *type_into(Element('button "Send"'), " ", slowly=True),
         question="press Send on localhost"),
    Case("submit button with no label of its own", *click(Element('button "Submit"')), question="press this button on localhost"),
    Case("form submit button whose label is not on the word list", *click(Element('button "Complete order"')),
         question="press Complete order on localhost"),
    Case("image submit button named by its alt text", *click(Element('button "Continue"')),
         question="press Continue on localhost"),
    Case("icon named by aria-labelledby", *click(Element('button "Delete repository"')),
         question="press Delete repository on localhost"),
    Case("icon named by the title inside its svg", *click(Element('button "Remove item"')),
         question="press Remove item on localhost"),
    Case("button whose text is slotted into a web component", *click(Element('button "Delete workspace"')),
         question="press Delete workspace on localhost"),
    Case("button on a read-only island inside an editor", *click(Element('button "Delete page"')),
         question="press Delete page on localhost"),
    Case("button inside the label of a checkbox", *click(Element('button "Delete history"')),
         question="press Delete history on localhost"),
    Case("fill_form ticking a menu item as a checkbox", "browser_fill_form", {"fields": [
        {"name": "Delete account", "type": "checkbox", "target": Element('menuitemcheckbox "Delete account"'), "value": "true"}]},
         question="press Delete account on localhost"),
    Case("button whose label ends in Subscribe after 200 characters", "browser_click",  # the server also takes a CSS selector
         {"target": "#digest", "element": "Weekly digest"},
         question=f"press {DIGEST_CARD[:SPOKEN_LABEL_CHARACTERS]} on localhost"),
]

REFUSED = [
    Case("type into a password field", *type_into(Element('textbox "Password"'), "hunter2"), returns=SECRET_REFUSAL),
    Case("fill a card number field", *fill({Element('textbox "Card"'): "4111"}), returns=SECRET_REFUSAL),
    Case("press a key in a one-time-code field", *press("1"), before=(click(Element('textbox "Code"')),),
         returns=SECRET_REFUSAL),
    Case("type at the label around a password field", "browser_type",  # the server also takes a CSS selector as a target
         {"target": "label:has([name=pw])", "element": "Password", "text": "hunter2"}, returns=SECRET_REFUSAL),
    Case("type into a password field shown as text", *type_into(Element('textbox "Passphrase"'), "hunter2"),
         returns=SECRET_REFUSAL),
    Case("stale ref", "browser_click", {"target": "e999", "element": "gone"},
         returns=f"{SERVER_ERROR}\nError: Ref e999 not found"),
    Case("navigate to a file", "browser_navigate", {"url": "file:///etc/hosts"},
         returns="Only http and https addresses are allowed."),
    Case("navigate to loopback", "browser_navigate", {"url": "http://127.0.0.1/form.html"},
         returns="Private and loopback addresses are blocked."),
    Case("navigate to script shaped like host:port", "browser_navigate", {"url": "javascript:1/log('SCRIPT')"},
         returns="Only http and https addresses are allowed."),
    Case("open a blocked host's subdomain in a new tab", "browser_tabs",
         {"action": "new", "url": f"https://www.{BLOCKED_HOST}/"}, returns=f"www.{BLOCKED_HOST} is blocked."),
    Case("a tool the model is never given", EVALUATE_TOOL, {"function": "() => 1"},
         returns=f"Unknown tool {EVALUATE_TOOL}."),
]

case_name = operator.attrgetter("name")


@pytest.mark.parametrize("case", HARMLESS, ids=case_name)
def test_harmless_call_runs_without_a_question(browser: Browser, case: Case) -> None:
    outcome = attempt_case(browser, case)
    assert outcome.asked == [], f"a harmless call made the user answer a question: {outcome}"
    assert outcome.returned.startswith(case.returns.format(site=browser.site)), f"the call did not run: {outcome}"
    assert outcome.page == case.page, f"the page is not where the call should have left it: {outcome}"


@pytest.mark.parametrize("case", IRREVERSIBLE, ids=case_name)
def test_irreversible_call_asks_once_and_a_no_leaves_the_page_untouched(browser: Browser, case: Case) -> None:
    outcome = attempt_case(browser, case)
    assert outcome.asked == [QUESTION.format(action=case.question)], f"wrong questions: {outcome}"
    assert outcome.returned == DECLINED, f"the model was not told the user said no: {outcome}"
    assert outcome.page == UNTOUCHED, f"the page changed although the user said no: {outcome}"


@pytest.mark.parametrize("case", REFUSED, ids=case_name)
def test_forbidden_call_is_refused_without_a_question(browser: Browser, case: Case) -> None:
    outcome = attempt_case(browser, case)
    assert outcome.asked == [], f"a call that is never allowed was offered to the user: {outcome}"
    assert outcome.returned.startswith(case.returns), f"the model did not read the refusal: {outcome}"
    assert outcome.page == UNTOUCHED, f"the page changed although the call was refused: {outcome}"


def test_accepting_a_confirm_dialog_asks_and_a_no_leaves_it_open(browser: Browser) -> None:
    browser.open_form()
    browser.call(*click(CLOSE_ACCOUNT))
    asked, returned = browser.use("browser_handle_dialog", {"accept": True})
    assert asked == [DIALOG_QUESTION], f"asked {asked}, returned {returned!r}"
    assert returned == DECLINED, f"asked {asked}, returned {returned!r}"
    browser.call("browser_handle_dialog", {"accept": False})  # fails unless the dialog is still waiting
    page = browser.page()
    assert page == UNTOUCHED, f"the account was deleted although the user said no: {page}"


def test_upload_from_outside_the_upload_folder_is_refused(browser: Browser) -> None:
    browser.open_form()
    browser.call(*click(RESUME))
    outside = browser.home / "work" / "other.txt"
    asked, returned = browser.use("browser_file_upload", {"paths": [str(outside)]})
    assert asked == [], f"asked {asked}, returned {returned!r}"
    assert returned.startswith(f"{SERVER_ERROR}\nError: File access denied: {outside}"), f"returned {returned!r}"
    browser.call("browser_file_upload", {})  # cancels the file chooser, which is still open
    attached, page = browser.attached_files(), browser.page()
    assert attached == [] and page == UNTOUCHED, f"attached {attached}, {page}"


def test_find_may_not_save_its_results_over_a_file_in_the_upload_folder(browser: Browser) -> None:
    browser.open_form()
    resume = browser.home / "uploads" / "resume.pdf"
    before = resume.read_bytes()
    outcome = browser.attempt("browser_find", {"text": "Submit", "filename": resume.name})
    assert outcome.asked == [] and outcome.returned == FILE_REFUSAL, f"saving was not refused: {outcome}"
    assert resume.read_bytes() == before, f"the resume was overwritten with {resume.read_bytes()!r}"


def test_upload_from_the_upload_folder_needs_no_question(browser: Browser) -> None:
    browser.open_form()
    browser.call(*click(RESUME))
    outcome = browser.attempt("browser_file_upload", {"paths": [str(browser.home / "uploads" / "resume.pdf")]})
    attached = browser.attached_files()
    assert outcome.asked == [], f"attaching the resume made the user answer a question: {outcome}"
    assert outcome.returned.startswith(PAGE_REPORT.format(site=browser.site) + FORM), f"the upload failed: {outcome}"
    assert outcome.page == UNTOUCHED and attached == ["resume.pdf"], f"attached {attached}, {outcome}"


def test_a_yes_submits_the_form_and_the_question_names_what_it_carries(browser: Browser) -> None:
    browser.open_form()
    browser.call(*fill({FULL_NAME: "Pierce Luu"}))
    browser.call(*type_into(COVER_LETTER, "Dear team"))
    browser.call(*click(RESUME))
    browser.call("browser_file_upload", {"paths": [str(browser.home / "uploads" / "resume.pdf")]})
    outcome = browser.attempt(*click(SUBMIT), says_yes=True)
    carried = "press Submit application on localhost, with resume.pdf attached and 1 written answer"
    assert outcome.asked == [QUESTION.format(action=carried)], f"wrong questions: {outcome}"
    assert outcome.returned == NOTHING_TO_SAY, f"the click did not run after the yes: {outcome}"
    assert outcome.page == Page(FORM, "LOG: SUBMITTED:Pierce Luu:resume.pdf", 0), f"the form was not submitted: {outcome}"


def test_a_yes_presses_the_button_inside_a_cross_origin_iframe(browser: Browser) -> None:
    browser.open_form()
    outcome = browser.attempt(*click(Element('button "Send message"', 1)), says_yes=True)
    assert outcome.asked == [QUESTION.format(action="press Send message on 127.0.0.1")], f"wrong questions: {outcome}"
    assert outcome.returned == NOTHING_TO_SAY, f"the click did not run after the yes: {outcome}"
    assert outcome.page == Page(FORM, "LOG:", 1), f"exactly the embedded form should have been sent: {outcome}"


@pytest.mark.parametrize(("reply", "returns"), [("five five five", "The user said: five five five"), (None, NO_ANSWER)],
                         ids=["answered", "silence"])
def test_ask_user_speaks_the_question_and_returns_the_answer(browser: Browser, reply: str | None, returns: str) -> None:
    browser.open_form()
    outcome = browser.attempt("ask_user", {"question": SPOKEN_QUESTION}, reply=reply)
    assert outcome.asked == [SPOKEN_QUESTION], f"the user did not hear the model's question: {outcome}"
    assert outcome.returned == returns, f"the model read the wrong answer: {outcome}"
    assert outcome.page == UNTOUCHED, f"asking the user changed the page: {outcome}"


def test_media_playing_is_true_only_while_unmuted_sound_plays(browser: Browser) -> None:
    browser.open_form()  # the form holds a muted, paused video
    before = browser.run(browser.agent.media_playing())
    started = browser.call(EVALUATE_TOOL, {"function": START_AUDIO_JS})
    after = browser.run(browser.agent.media_playing())
    assert (before, after) == (False, True), f"playing before: {before}, after: {after}; starting audio returned {started!r}"


def test_closing_the_window_in_the_middle_of_a_task_raises(browser: Browser) -> None:
    browser.open_form()
    browser.run(browser.agent._page())  # the task has seen a real page
    browser.call("browser_tabs", {"action": "close"})
    with pytest.raises(BrowserClosedError):
        browser.run(browser.agent._page())
