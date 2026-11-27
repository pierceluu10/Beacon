"""The app tools without touching the real apps: AppleScript and the running-app list are faked."""
import asyncio
from types import SimpleNamespace

import pytest

import apps


@pytest.fixture
def scripts(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Every AppleScript run, as (script, *arguments); a contact lookup finds Mom's number."""
    ran = []

    async def osascript(script: str, *arguments: str) -> str:
        ran.append((script, *arguments))
        return "+1 555 0100" if script == apps.CONTACT else ""

    monkeypatch.setattr(apps, "osascript", osascript)
    return ran


def answering(yes: bool, asked: list[str]):
    async def confirm(question: str) -> bool:
        asked.append(question)
        return yes
    return confirm


@pytest.mark.parametrize("yes", [True, False])
def test_a_text_to_a_contact_is_sent_only_after_a_spoken_yes(scripts: list, yes: bool) -> None:
    asked = []
    said = asyncio.run(apps.use("messages", {"action": "send", "to": "Mom", "text": "Running late"}, answering(yes, asked)))
    sent = [run for run in scripts if run[0] == apps.MESSAGES_SEND]
    print(said, asked, scripts)
    assert asked == ["Text Mom saying: Running late?"], asked
    assert sent == ([(apps.MESSAGES_SEND, "+1 555 0100", "Running late")] if yes else []), sent
    assert said == ("Sent to Mom." if yes else apps.DECLINED)


def test_an_email_address_is_used_as_given_and_the_mail_waits_for_a_yes(scripts: list) -> None:
    asked = []
    said = asyncio.run(apps.use("mail", {"action": "send", "to": "bo@example.com", "subject": "Lunch", "body": "Noon?"},
                                answering(False, asked)))
    assert asked == ["Email bo@example.com about Lunch?"] and said == apps.DECLINED and scripts == [], (asked, scripts)


def test_a_contact_without_a_number_is_reported_rather_than_guessed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def osascript(script: str, *arguments: str) -> str:
        return ""

    monkeypatch.setattr(apps, "osascript", osascript)
    said = asyncio.run(apps.use("messages", {"action": "send", "to": "Zed", "text": "hi"}, answering(True, [])))
    assert said.startswith("Error: No contact named 'Zed'"), said


@pytest.mark.parametrize("spotify, script", [(True, 'tell application "Spotify" to pause'),
                                             (False, 'tell application "Music" to pause')])
def test_music_controls_spotify_when_it_is_open(monkeypatch: pytest.MonkeyPatch, scripts: list, spotify: bool,
                                                script: str) -> None:
    running = [SimpleNamespace(bundleIdentifier=lambda: apps.SPOTIFY if spotify else "com.apple.finder")]
    monkeypatch.setattr(apps, "NSWorkspace", SimpleNamespace(
        sharedWorkspace=lambda: SimpleNamespace(runningApplications=lambda: running)))
    asyncio.run(apps.use("music", {"action": "pause"}, answering(True, [])))
    assert scripts == [(script,)], scripts


def test_outside_the_app_calendar_says_so_instead_of_asking_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    """Asking for Calendar access from a process whose app declares no reason kills it outright."""
    monkeypatch.delenv(apps.IN_APP, raising=False)
    monkeypatch.setattr(apps, "_access", lambda entity: pytest.fail("asked macOS for access outside the app"))
    said = asyncio.run(apps.use("calendar", {"action": "list"}, answering(True, [])))
    assert said == f"Error: {apps.NEEDS_APP}", said


def test_in_the_app_a_bad_time_comes_back_as_an_error_before_anything_is_saved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(apps.IN_APP, "1")
    store = SimpleNamespace()
    monkeypatch.setattr(apps, "_access", lambda entity: store)
    said = asyncio.run(apps.use("calendar", {"action": "add", "title": "Dentist", "start": "next tuesday"},
                                answering(True, [])))
    assert said.startswith("Error: 'next tuesday' is not a time"), said


def test_a_malformed_call_comes_back_as_an_error_to_work_around(scripts: list) -> None:
    missing = asyncio.run(apps.use("messages", {"action": "send"}, answering(True, [])))
    print(missing)
    assert missing.startswith("Error:") and scripts == [], missing


def test_every_app_tool_names_its_actions_and_none_deletes() -> None:
    for tool in apps.APP_TOOLS:
        actions = tool["input_schema"]["properties"]["action"]["enum"]
        assert actions and not {"delete", "remove"} & set(actions), tool
