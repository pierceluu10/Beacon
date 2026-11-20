"""What needs a spoken yes. Pure rules over facts read from the live DOM by inspect.js: no I/O, no model."""
import ipaddress
import re
import socket
from typing import Literal, NamedTuple

IRREVERSIBLE = re.compile(
    r"\b(submit|send|pay|buy|purchase|place (your |my |the )?order|order now|check[ -]?out|delete|remove|confirm|post|publish"
    r"|book|reserve|subscribe|unsubscribe|donate|transfer|withdraw|sign[ -]?up|register|accept|agree"
    r"|cancel (my|your|subscription|order|booking|reservation))\b", re.IGNORECASE)
# The server prints the message raw, so it may span lines; the first line that opens a dialog is the server's own.
DIALOG = re.compile(r'^- \["(?P<kind>\w+)" dialog with message "(?P<message>.*)"\]: can be handled by browser_handle_dialog',
                    re.MULTILINE | re.DOTALL)
SHORT_LABEL_WORDS = 4  # longer text on a link or a bare clickable is a title, not a command
SPOKEN_LABEL_CHARACTERS = 60
# Chrome and the server read every other spelling (user names, backslashes, escapes, non-ASCII, "javascript:1") their own way.
PLAIN_ADDRESS = re.compile(r"(https?://)?(?P<host>[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*)(?(1)(:\d+)?)([/?#].*)?", re.DOTALL)
ENTER_KEYS = frozenset({"Enter", "NumpadEnter", "\n", "\r"})  # Playwright presses Enter for both characters
SPACE_KEYS = frozenset({" ", "Space"})
INSPECTED_TOOLS = frozenset({"browser_click", "browser_type"})
UNREADABLE = "unreadable"  # the fact kind the agent reports when inspect.js could not run; its label says why
SECRET_REFUSAL = ("That is a password, card or verification-code field. Never fill those in: call ask_user and ask the user "
                  "to do it in the window.")
FILE_REFUSAL = "Saving results to a file is not allowed. Call the tool again without filename."


class Verdict(NamedTuple):
    decision: Literal["allow", "ask", "deny"]
    text: str = ""  # the question to speak, or the refusal the model reads


ALLOW = Verdict("allow")


def url_refusal(url: str, blocked_hosts: frozenset[str]) -> str | None:
    """Why the agent may not open url, or None when it may."""
    plain = PLAIN_ADDRESS.fullmatch(url)
    if not plain:
        return "Only http and https addresses are allowed."
    host = plain["host"].lower()
    if any(host == blocked or host.endswith(f".{blocked}") for blocked in (*map(str.lower, blocked_hosts), "localhost")):
        return f"{host} is blocked."
    try:
        address = ipaddress.ip_address(socket.inet_aton(host))  # also reads 127.1 and 2130706433, as Chrome does
    except OSError:  # a name, unless it ends like a number: Chrome reads "0x" as 0 and inet_aton does not
        return "That is not a valid web address." if host.rpartition(".")[2][0].isdigit() else None
    return None if address.is_global else "Private and loopback addresses are blocked."


def to_inspect(tool: str, arguments: dict) -> list[str | None]:
    """The elements whose facts judge() needs for this call: target refs, or None for whatever has keyboard focus."""
    if tool in INSPECTED_TOOLS:
        return [arguments.get("target", "")]
    if tool == "browser_fill_form":
        return [field.get("target", "") for field in arguments.get("fields", [])]
    return [None] if tool == "browser_press_key" else []


def _spoken(label: str) -> str:
    # "." and "," survive only inside a number, so "$1,249.00" is read as an amount
    return " ".join(re.sub(r"[^\w\s'&$%.,-]|(?<!\d)[.,]|[.,](?!\d)", " ", label).split())[:SPOKEN_LABEL_CHARACTERS]


def _ask(action: str, fact: dict) -> Verdict:
    """The question for a submit names what the form carries: attached files and filled-in long answers."""
    carries = [f"{name} attached" for name in fact["files"]]
    if fact["answers"]:
        carries.append(f"{fact['answers']} written answer{'s' * (fact['answers'] > 1)}")
    carried = f", with {' and '.join(carries)}" if carries else ""
    return Verdict("ask", f"Ready to {action} on {fact['host'] or 'this page'}{carried}. Say yes to go ahead, or no to cancel.")


def _click(fact: dict) -> Verdict:
    kind, label = fact["kind"], fact["label"]
    is_title = kind in ("link", "other") and len(label.split()) > SHORT_LABEL_WORDS
    if kind in ("button", "link", "other") and not is_title and (IRREVERSIBLE.search(label) or fact["submits"]):
        return _ask(f"press {_spoken(label) or 'this button'}", fact)
    return ALLOW


def _press(key: str, fact: dict) -> Verdict:
    if fact["kind"] == "frame":  # focus is inside a frame this page may not read: fail closed
        return _ask(f"press {key} in an embedded form", fact)
    if fact["kind"] in ("field", "toggle") and key in ENTER_KEYS:  # Enter on a checkbox or radio submits its form too
        return _ask(f"press Enter in {_spoken(fact['label']) or 'this form'}", fact)
    return _click(fact)


def judge(tool: str, arguments: dict, facts: list[dict]) -> Verdict:
    """Classify one browser call from the facts inspect.js returned for to_inspect(tool, arguments)."""
    if "filename" in arguments:  # the server would write it into the upload folder, over the resume if so named
        return Verdict("deny", FILE_REFUSAL)
    for fact in facts:
        if fact["kind"] == UNREADABLE:  # fail closed, and the model reads the server's reason
            return Verdict("deny", fact["label"])
        if fact["kind"] == "secret" and tool != "browser_click":
            return Verdict("deny", SECRET_REFUSAL)
    if tool == "browser_click":
        return _click(facts[0])
    if tool == "browser_fill_form":  # a field filled as a checkbox is clicked
        return next((verdict for verdict in map(_click, facts) if verdict != ALLOW), ALLOW)
    pressed = set(arguments.get("key", "").split("+")) if tool == "browser_press_key" else set()  # a chord presses every part
    if tool == "browser_type":
        pressed = set(arguments.get("text", "")) if arguments.get("slowly") else set()  # typed slowly, each character is a key
        pressed |= {"Enter"} if arguments.get("submit") else set()
    if pressed & ENTER_KEYS:
        return _press("Enter", facts[0])
    return _press("Space", facts[0]) if pressed & SPACE_KEYS else ALLOW


def judge_dialog(modal_state: str) -> Verdict:
    """Accepting a JavaScript dialog: alerts have one outcome, everything else is the page asking for a yes."""
    dialog = DIALOG.search(modal_state)
    if dialog and dialog["kind"] == "alert":
        return ALLOW
    message = _spoken(dialog["message"]) if dialog else "something I could not read"
    return Verdict("ask", f"The page asks: {message}. Say yes to accept, or no to cancel.")
