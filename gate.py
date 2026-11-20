"""What needs a spoken yes. Pure rules over facts read from the live DOM by inspect.js: no I/O, no model."""
import ipaddress
import re
import socket
from typing import Literal, NamedTuple
from urllib.parse import urlsplit

IRREVERSIBLE = re.compile(
    r"\b(submit|send|pay|buy|purchase|place (your |my )?order|order now|check ?out|delete|remove|confirm|post|publish"
    r"|book|reserve|subscribe|unsubscribe|donate|transfer|withdraw|sign up|register|accept|agree"
    r"|cancel (my|your|subscription|order|booking|reservation))\b", re.IGNORECASE)
FORM_SUBMIT_ONLY = re.compile(r"\bapply\b", re.IGNORECASE)  # "Apply" opens a form as a link and sends one as its submit button
DIALOG = re.compile(r'^- \["(?P<kind>\w+)" dialog with message "(?P<message>.*)"\]: can be handled by browser_handle_dialog',
                    re.MULTILINE)
SHORT_LABEL_WORDS = 4  # longer text on a link or a bare clickable is a title, not a command
SPOKEN_LABEL_CHARACTERS = 60
ALLOWED_SCHEMES = frozenset({"http", "https"})
ENTER_KEYS = frozenset({"Enter", "NumpadEnter"})
SPACE_KEYS = frozenset({" ", "Space"})
INSPECTED_TOOLS = frozenset({"browser_click", "browser_type"})
UNREADABLE = "unreadable"  # the fact kind the agent reports when inspect.js could not run; its label says why
SECRET_REFUSAL = ("That is a password, card or verification-code field. Never fill those in: call ask_user and ask the user "
                  "to do it in the window.")


class Verdict(NamedTuple):
    decision: Literal["allow", "ask", "deny"]
    text: str = ""  # the question to speak, or the refusal the model reads


ALLOW = Verdict("allow")


def url_refusal(url: str, blocked_hosts: frozenset[str]) -> str | None:
    """Why the agent may not open url, or None when it may."""
    try:
        parts = urlsplit(url if "://" in url else f"https://{url}")
        host, _ = (parts.hostname or "").lower(), parts.port  # .port raises on "javascript:alert(1)"
    except ValueError:
        return "That is not a valid web address."
    if parts.scheme not in ALLOWED_SCHEMES or not host:
        return "Only http and https addresses are allowed."
    if any(host == blocked or host.endswith(f".{blocked}") for blocked in (*blocked_hosts, "localhost")):
        return f"{host} is blocked."
    try:
        address = ipaddress.ip_address(socket.inet_aton(host))  # also reads 127.1 and 2130706433, as Chrome does
    except OSError:
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return None
    return None if address.is_global else "Private and loopback addresses are blocked."


def to_inspect(tool: str, arguments: dict) -> list[str | None]:
    """The elements whose facts judge() needs for this call: target refs, or None for whatever has keyboard focus."""
    if tool in INSPECTED_TOOLS:
        return [arguments.get("target", "")]
    if tool == "browser_fill_form":
        return [field.get("target", "") for field in arguments.get("fields", [])]
    return [None] if tool == "browser_press_key" else []


def _spoken(label: str) -> str:
    return " ".join(re.sub(r"[^\w\s'&-]", " ", label).split())[:SPOKEN_LABEL_CHARACTERS]


def _ask(action: str, fact: dict) -> Verdict:
    """The question for a submit names what the form carries: attached files and filled-in long answers."""
    carries = [f"{name} attached" for name in fact["files"]]
    if fact["answers"]:
        carries.append(f"{fact['answers']} written answer{'s' * (fact['answers'] > 1)}")
    carried = f", with {' and '.join(carries)}" if carries else ""
    return Verdict("ask", f"Ready to {action} on {fact['host']}{carried}. Say yes to go ahead, or no to cancel.")


def _click(fact: dict) -> Verdict:
    kind, label = fact["kind"], fact["label"]
    is_title = kind in ("link", "other") and len(label.split()) > SHORT_LABEL_WORDS
    if kind in ("button", "link", "other") and not is_title and (
            IRREVERSIBLE.search(label) or fact["submits"] and FORM_SUBMIT_ONLY.search(label)):
        return _ask(f"press {_spoken(label) or 'this button'}", fact)
    return ALLOW


def _press(key: str, fact: dict) -> Verdict:
    if fact["kind"] == "frame":  # focus is inside a frame this page may not read: fail closed
        return _ask(f"press {key} in an embedded form", fact)
    if fact["kind"] == "field" and key in ENTER_KEYS:
        return _ask(f"press Enter in {_spoken(fact['label'])}", fact)
    return _click(fact)


def judge(tool: str, arguments: dict, facts: list[dict]) -> Verdict:
    """Classify one browser call from the facts inspect.js returned for to_inspect(tool, arguments)."""
    for fact in facts:
        if fact["kind"] == UNREADABLE:  # fail closed, and the model reads the server's reason
            return Verdict("deny", fact["label"])
        if fact["kind"] == "secret" and tool != "browser_click":
            return Verdict("deny", SECRET_REFUSAL)
    if tool == "browser_click":
        return _click(facts[0])
    if tool == "browser_type" and (arguments.get("submit") or arguments.get("slowly") and "\n" in arguments.get("text", "")):
        return _press("Enter", facts[0])
    if tool == "browser_press_key":
        key = arguments.get("key", "").split("+")[-1]  # "Control+Enter" presses Enter
        if key in ENTER_KEYS | SPACE_KEYS:
            return _press("Enter" if key in ENTER_KEYS else "Space", facts[0])
    return ALLOW


def judge_dialog(modal_state: str) -> Verdict:
    """Accepting a JavaScript dialog: alerts have one outcome, everything else is the page asking for a yes."""
    dialog = DIALOG.search(modal_state)
    if dialog and dialog["kind"] == "alert":
        return ALLOW
    message = _spoken(dialog["message"]) if dialog else "something I could not read"
    return Verdict("ask", f"The page asks: {message}. Say yes to accept, or no to cancel.")
