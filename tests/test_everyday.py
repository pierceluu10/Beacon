"""front_tab against stand-in scripts run by the real osascript: no Chrome, no permission prompt."""
import asyncio
import subprocess
from pathlib import Path

import pytest

import everyday

PROFILE = Path("/nowhere/chrome-profile")


def answer(monkeypatch, tmp_path, body: str):
    script = tmp_path / "stand_in.js"
    script.write_text(f"function run(argv) {{ {body} }}")
    monkeypatch.setattr(everyday, "FRONT_TAB_SCRIPT", script)
    return asyncio.run(everyday.front_tab(PROFILE))


def stand_ins_running(tmp_path) -> str:
    return subprocess.run(["pgrep", "-f", str(tmp_path)], capture_output=True, text=True).stdout


def test_address_and_title(monkeypatch, tmp_path):
    assert answer(monkeypatch, tmp_path, r'return "https://jobs.example/1?a=b\nEngineer – Acme 職位";') == (
        "https://jobs.example/1?a=b", "Engineer – Acme 職位")


def test_page_still_loading_has_no_title(monkeypatch, tmp_path):
    assert answer(monkeypatch, tmp_path, r'return "https://jobs.example/1\n";') == ("https://jobs.example/1", "")


def test_nothing_in_front(monkeypatch, tmp_path):
    assert answer(monkeypatch, tmp_path, 'return "";') is None


def test_script_gets_the_agent_profile(monkeypatch, tmp_path):
    assert answer(monkeypatch, tmp_path, r'return argv[0] + "\n";') == (str(PROFILE), "")


def test_permission_denied(monkeypatch, tmp_path):
    with pytest.raises(everyday.EverydayBrowserError, match="Automation"):
        answer(monkeypatch, tmp_path, 'throw new Error("-1743");')


def test_other_script_failure_is_nothing(monkeypatch, tmp_path):
    assert answer(monkeypatch, tmp_path, 'throw new Error("Application isn’t running. (-600)");') is None


def test_blocked_script_is_killed(monkeypatch, tmp_path):
    monkeypatch.setattr(everyday, "FRONT_TAB_SECONDS", 0.3)
    with pytest.raises(everyday.EverydayBrowserError, match="Allow"):
        answer(monkeypatch, tmp_path, "delay(30);")
    assert stand_ins_running(tmp_path) == ""


def test_cancelled_read_is_killed(monkeypatch, tmp_path):
    script = tmp_path / "stand_in.js"
    script.write_text("function run() { delay(30); }")
    monkeypatch.setattr(everyday, "FRONT_TAB_SCRIPT", script)

    async def stop_early() -> None:
        reading = asyncio.create_task(everyday.front_tab(PROFILE))
        await asyncio.sleep(0.2)
        reading.cancel()
        with pytest.raises(asyncio.CancelledError):
            await reading

    asyncio.run(stop_early())
    assert stand_ins_running(tmp_path) == ""
