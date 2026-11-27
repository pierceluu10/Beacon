"""Mac apps without the screen: Calendar and Reminders through EventKit, Notes, Music, Spotify, Messages and Mail
through AppleScript. Each takes a second or less where clicking through the app takes many steps, and works in every
mode. Sending a message or an email asks the user first; nothing here deletes."""
import asyncio
import os
import re
import threading
from datetime import datetime, timedelta
from typing import Awaitable, Callable

import EventKit
from AppKit import NSWorkspace
from Foundation import (NSCalendar, NSCalendarUnitDay, NSCalendarUnitHour, NSCalendarUnitMinute,
                        NSCalendarUnitMonth, NSCalendarUnitYear, NSDate)

SCRIPT_TIMEOUT_SECONDS = 30  # the first use of an app waits on macOS's Automation prompt
ACCESS_TIMEOUT_SECONDS = 60  # time to answer macOS's Calendar or Reminders prompt
MAX_DAYS = 31
MAX_LISTED = 25
NOTE_PREVIEW_CHARACTERS = 200
SPOKEN_TEXT_CHARACTERS = 120  # how much of a message the confirmation question reads out
DEFAULT_EVENT = timedelta(hours=1)
SPOTIFY = "com.spotify.client"
HANDLE = re.compile(r"^(\+?[\d\s().-]{6,}|[^@\s]+@[^@\s]+\.[^@\s]+)$")  # a phone number or an email address
# macOS kills a process that asks for Calendar or Reminders access unless the app it runs under declares why; the
# Beacon app does and sets this, a terminal does not.
IN_APP = "BEACON_APP"
NEEDS_APP = ("Calendar and Reminders need the Beacon app; from a terminal they are not available. Use the browser or "
             "the screen instead.")
DECLINED = "The user said no. Do not retry or work around it. Stop and tell the user what you left undone."
NO_ACCESS = ("macOS has not given Beacon access to {app}. Tell the user to allow it in System Settings, Privacy and "
             "Security, {app}.")

Confirm = Callable[[str], Awaitable[bool]]


class AppError(Exception):
    """The app could not do it; the model reads the message and tries another way."""


def _action(name: str, actions: list[str], description: str, properties: dict, required: tuple[str, ...] = ()) -> dict:
    schema = {"type": "object", "properties": {"action": {"type": "string", "enum": actions}, **properties},
              "required": ["action", *required]}
    return {"name": name, "description": description, "input_schema": schema}


WHEN = {"type": "string", "description": "Local time as YYYY-MM-DDTHH:MM"}
TEXT = {"type": "string"}
APP_TOOLS = [
    _action("calendar", ["list", "add"],
            "The user's calendars. list: events from today for `days` days. add: one event on the default calendar.",
            {"days": {"type": "integer", "minimum": 1, "maximum": MAX_DAYS}, "title": TEXT, "start": WHEN, "end": WHEN,
             "location": TEXT}),
    _action("reminders", ["list", "add", "complete"],
            "The user's reminders. list: the open ones. add: a new one, with an optional due time. complete: tick off "
            "the open one whose title contains `title`.", {"title": TEXT, "due": WHEN}),
    _action("notes", ["add", "search"], "Apple Notes. add: a new note. search: notes whose title or text contain `query`.",
            {"title": TEXT, "body": TEXT, "query": TEXT}),
    _action("music", ["play", "pause", "next", "previous", "now_playing"],
            "Controls Spotify when it is open, otherwise Apple Music. play with `query` plays a matching playlist or song "
            "from the Apple Music library; for anything else on Spotify, use the browser.", {"query": TEXT}),
    _action("messages", ["send"], "Send an iMessage. `to` is a contact's name, a phone number or an email address.",
            {"to": TEXT, "text": TEXT}, ("to", "text")),
    _action("mail", ["send"], "Send an email from Apple Mail. `to` is a contact's name or an email address.",
            {"to": TEXT, "subject": TEXT, "body": TEXT}, ("to", "subject", "body")),
    _action("system", ["volume", "open_app"], "volume: set the output volume to `level` (0 to 100). open_app: open `app`.",
            {"level": {"type": "integer", "minimum": 0, "maximum": 100}, "app": TEXT}),
]
APPS = frozenset(tool["name"] for tool in APP_TOOLS)


async def osascript(script: str, *arguments: str) -> str:
    """Run AppleScript with arguments passed to its `on run` handler, never pasted into the script."""
    process = await asyncio.create_subprocess_exec("osascript", "-e", script, *arguments,
                                                   stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, error = await asyncio.wait_for(process.communicate(), SCRIPT_TIMEOUT_SECONDS)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise AppError("The app did not answer in time.") from None
    if process.returncode:
        raise AppError(error.decode().strip() or "The app refused.")
    return out.decode().strip()


def _when(text: str) -> datetime:
    try:
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        raise AppError(f"{text!r} is not a time as YYYY-MM-DDTHH:MM.") from None


def _nsdate(moment: datetime) -> NSDate:
    return NSDate.dateWithTimeIntervalSince1970_(moment.timestamp())


def _moment(date: NSDate) -> datetime:
    return datetime.fromtimestamp(date.timeIntervalSince1970())


_store = EventKit.EKEventStore.alloc().init()
_granted: set[int] = set()


def _access(entity: int) -> EventKit.EKEventStore:
    """The event store once macOS allows this entity, asking the user the first time."""
    if entity not in _granted:
        answered, granted = threading.Event(), []
        request = (_store.requestFullAccessToEventsWithCompletion_ if entity == EventKit.EKEntityTypeEvent
                   else _store.requestFullAccessToRemindersWithCompletion_)
        request(lambda allowed, error: (granted.append(allowed), answered.set()))
        answered.wait(ACCESS_TIMEOUT_SECONDS)
        if not (granted and granted[0]):
            raise AppError(NO_ACCESS.format(app="Calendars" if entity == EventKit.EKEntityTypeEvent else "Reminders"))
        _granted.add(entity)
    return _store


def _saved(result: tuple) -> None:
    saved, error = result
    if not saved:
        raise AppError(str(error.localizedDescription()) if error else "It could not be saved.")


def calendar(arguments: dict) -> str:
    store = _access(EventKit.EKEntityTypeEvent)
    if arguments["action"] == "list":
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=min(int(arguments.get("days", 1)), MAX_DAYS))
        found = store.eventsMatchingPredicate_(store.predicateForEventsWithStartDate_endDate_calendars_(
            _nsdate(start), _nsdate(end), None))
        events = sorted(found or [], key=lambda event: event.startDate().timeIntervalSince1970())
        lines = [f"{_moment(event.startDate()):%a %d %b}, all day: {event.title()}" if event.isAllDay() else
                 f"{_moment(event.startDate()):%a %d %b %H:%M}-{_moment(event.endDate()):%H:%M}: {event.title()}"
                 + (f" at {event.location()}" if event.location() else "") for event in events[:MAX_LISTED]]
        return "\n".join(lines) or "No events."
    start = _when(arguments.get("start"))
    end = _when(arguments["end"]) if arguments.get("end") else start + DEFAULT_EVENT
    event = EventKit.EKEvent.eventWithEventStore_(store)
    event.setTitle_(arguments.get("title") or "Event")
    event.setStartDate_(_nsdate(start))
    event.setEndDate_(_nsdate(end))
    if arguments.get("location"):
        event.setLocation_(arguments["location"])
    event.setCalendar_(store.defaultCalendarForNewEvents())
    _saved(store.saveEvent_span_error_(event, EventKit.EKSpanThisEvent, None))
    return f"Added {event.title()} on {start:%a %d %b at %H:%M}."


def _open_reminders(store: EventKit.EKEventStore) -> list:
    fetched, found = threading.Event(), []
    predicate = store.predicateForIncompleteRemindersWithDueDateStarting_ending_calendars_(None, None, None)
    store.fetchRemindersMatchingPredicate_completion_(predicate, lambda reminders: (found.extend(reminders or []),
                                                                                     fetched.set()))
    fetched.wait(SCRIPT_TIMEOUT_SECONDS)
    return found


def reminders(arguments: dict) -> str:
    store = _access(EventKit.EKEntityTypeReminder)
    action, title = arguments["action"], arguments.get("title") or ""
    if action == "list":
        return "\n".join(reminder.title() for reminder in _open_reminders(store)[:MAX_LISTED]) or "No open reminders."
    if action == "complete":
        match = next((reminder for reminder in _open_reminders(store) if title.lower() in reminder.title().lower()), None)
        if not title or match is None:
            raise AppError(f"No open reminder contains {title!r}.")
        match.setCompleted_(True)
        _saved(store.saveReminder_commit_error_(match, True, None))
        return f"Ticked off {match.title()}."
    reminder = EventKit.EKReminder.reminderWithEventStore_(store)
    reminder.setTitle_(title or "Reminder")
    reminder.setCalendar_(store.defaultCalendarForNewReminders())
    if arguments.get("due"):
        units = NSCalendarUnitYear | NSCalendarUnitMonth | NSCalendarUnitDay | NSCalendarUnitHour | NSCalendarUnitMinute
        reminder.setDueDateComponents_(NSCalendar.currentCalendar().components_fromDate_(
            units, _nsdate(_when(arguments["due"]))))
    _saved(store.saveReminder_commit_error_(reminder, True, None))
    return f"Added the reminder {reminder.title()}."


NOTES_ADD = 'on run {title, body}\ntell application "Notes" to make new note with properties {name:title, body:body}\nend run'
NOTES_SEARCH = f'''on run {{query}}
set found to ""
tell application "Notes"
  repeat with found_note in (notes whose name contains query or plaintext contains query)
    set preview to plaintext of found_note
    if length of preview > {NOTE_PREVIEW_CHARACTERS} then set preview to text 1 thru {NOTE_PREVIEW_CHARACTERS} of preview
    set found to found & name of found_note & ": " & preview & linefeed
  end repeat
end tell
return found
end run'''
MUSIC_PLAY = '''on run {query}
tell application "Music"
  set matches to (user playlists whose name contains query)
  if matches is {} then set matches to (tracks of library playlist 1 whose name contains query or artist contains query)
  if matches is {} then return "Nothing in the Apple Music library matches."
  play item 1 of matches
  return "Playing " & name of item 1 of matches & "."
end tell
end run'''
PLAYER = {"pause": "pause", "next": "next track", "previous": "previous track", "play": "play"}
NOW_PLAYING = ('tell application "{app}"\nif player state is stopped then return "Nothing is playing."\n'
               'return name of current track & " by " & artist of current track\nend tell')
CONTACT = '''on run {wanted, kind}
tell application "Contacts"
  set matches to (people whose name contains wanted)
  if matches is {} then return ""
  if kind is "email" then return value of first email of item 1 of matches
  return value of first phone of item 1 of matches
end tell
end run'''
MESSAGES_SEND = '''on run {handle, message}
tell application "Messages"
  send message to participant handle of (1st account whose service type = iMessage)
end tell
end run'''
MAIL_SEND = '''on run {address, subject, body}
tell application "Mail"
  set outgoing to make new outgoing message with properties {subject:subject, content:body & return, visible:false}
  tell outgoing to make new to recipient at end of to recipients with properties {address:address}
  send outgoing
end tell
end run'''


async def notes(arguments: dict) -> str:
    if arguments["action"] == "add":
        await osascript(NOTES_ADD, arguments.get("title") or "Note", arguments.get("body") or "")
        return "Note added."
    return await osascript(NOTES_SEARCH, arguments.get("query") or "") or "No notes match."


async def music(arguments: dict) -> str:
    action = arguments["action"]
    spotify = any(app.bundleIdentifier() == SPOTIFY for app in NSWorkspace.sharedWorkspace().runningApplications())
    player = "Spotify" if spotify else "Music"
    if action == "play" and arguments.get("query"):
        if spotify:
            raise AppError("Spotify cannot search from here. Open open.spotify.com in the browser instead.")
        return await osascript(MUSIC_PLAY, arguments["query"])
    if action == "now_playing":
        return await osascript(NOW_PLAYING.format(app=player))
    await osascript(f'tell application "{player}" to {PLAYER[action]}')
    return "Done."


async def _handle(to: str, kind: str) -> str:
    """A phone number or email address as given, or the first one on the contact card whose name contains `to`."""
    if HANDLE.match(to.strip()):
        return to.strip()
    found = await osascript(CONTACT, to, kind)
    if not found:
        raise AppError(f"No contact named {to!r} has a {'email address' if kind == 'email' else 'phone number'}. "
                       "Ask the user for it.")
    return found


async def messages(arguments: dict, confirm: Confirm) -> str:
    to, text = str(arguments["to"]), str(arguments["text"])
    handle = await _handle(to, "phone")
    if not await confirm(f"Text {to} saying: {text[:SPOKEN_TEXT_CHARACTERS]}?"):
        return DECLINED
    await osascript(MESSAGES_SEND, handle, text)
    return f"Sent to {to}."


async def mail(arguments: dict, confirm: Confirm) -> str:
    to, subject = str(arguments["to"]), str(arguments["subject"])
    address = await _handle(to, "email")
    if not await confirm(f"Email {to} about {subject[:SPOKEN_TEXT_CHARACTERS]}?"):
        return DECLINED
    await osascript(MAIL_SEND, address, subject, str(arguments["body"]))
    return f"Emailed {to}."


async def system(arguments: dict) -> str:
    if arguments["action"] == "volume":
        level = max(0, min(100, int(arguments["level"])))
        await osascript(f"set volume output volume {level}")
        return f"Volume set to {level}."
    process = await asyncio.create_subprocess_exec("open", "-a", str(arguments["app"]), stderr=asyncio.subprocess.PIPE)
    _, error = await process.communicate()
    if process.returncode:
        raise AppError(error.decode().strip() or f"There is no app called {arguments['app']}.")
    return f"Opened {arguments['app']}."


async def use(tool: str, arguments: dict, confirm: Confirm) -> str:
    """Carry out one app tool call and return what the model reads back; failures come back as text to work around."""
    try:
        if tool in ("calendar", "reminders"):
            if not os.environ.get(IN_APP):
                raise AppError(NEEDS_APP)
            return await asyncio.to_thread(calendar if tool == "calendar" else reminders, arguments)
        if tool in ("messages", "mail"):
            return await (messages if tool == "messages" else mail)(arguments, confirm)
        return await {"notes": notes, "music": music, "system": system}[tool](arguments)
    except AppError as error:
        return f"Error: {error}"
    except (KeyError, TypeError, ValueError) as error:  # a malformed call
        return f"Error: the call is missing or has a wrong argument ({error!r})."
