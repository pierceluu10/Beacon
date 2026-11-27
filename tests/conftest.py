"""Shared fixtures: synthesized speech clips, two local web servers (the second gives a cross-origin iframe), scratch folders."""
import contextlib
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

import everyday
import mouth

SITE = Path(__file__).with_name("site")
CROSS_ORIGIN_PORT_PLACEHOLDER = "8772"
SAY_TIMEOUT_SECONDS = 10  # `say` occasionally hangs when writing a file; a killed run leaves a truncated clip
SAY_ATTEMPTS = 3
PORT_WAIT_ATTEMPTS = 100
PORT_WAIT_SECONDS = 0.1
CLIPS = {
    "wake": "Hey Jarvis.",
    "wakestop": "Hey Jarvis, stop.",
    "wakecmd": "Hey Jarvis, go to YouTube and search for lo-fi beats.",
    "travis": "Hey Travis, how was your weekend?",
    "yes": "Yes.",
    "no": "No.",
    "nostop": "No, stop.",
    "stop": "Stop.",
    "thanks": "Thanks.",
    "pause_700": "Hey Jarvis, go to YouTube [[slnc 700]] and search for lo-fi beats.",
    "command": "Hey Jarvis, fill in this application with my name and email, attach my resume, and submit it.",
    "followup": "Which company was that application for?",
    "slow": "Hey Jarvis, wait for the countdown on this page to finish, then tell me what it says.",
}
FACTS = "Name: Pierce Luu\nEmail: pierce@example.com\nPhone: 555 0100\nLocation: Toronto\n"
RESUME = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _synthesize(text: str, path: Path) -> None:
    for _ in range(SAY_ATTEMPTS):
        try:
            subprocess.run(["/usr/bin/say", "-v", "Samantha", "-o", str(path), "--data-format=LEI16@16000", text],
                           check=True, timeout=SAY_TIMEOUT_SECONDS)
            return
        except subprocess.TimeoutExpired:
            continue
    raise RuntimeError(f"say could not synthesize {path.name}")


@pytest.fixture(scope="session")
def clips(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A folder of 16 kHz mono clips, one per CLIPS key, spoken by macOS say."""
    folder = tmp_path_factory.mktemp("clips")
    for name, text in CLIPS.items():
        _synthesize(text, folder / f"{name}.wav")
    return folder


@pytest.fixture(scope="session")
def site(tmp_path_factory: pytest.TempPathFactory):
    """Base address of tests/site served on localhost; a second server on another port serves the cross-origin frame."""
    root = tmp_path_factory.mktemp("site")
    main_port, frame_port = _free_port(), _free_port()
    for page in SITE.iterdir():
        (root / page.name).write_text(page.read_text().replace(CROSS_ORIGIN_PORT_PLACEHOLDER, str(frame_port)))
    servers = [subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"], cwd=root,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for port in (main_port, frame_port)]
    for port in (main_port, frame_port):
        _wait_for_port(port)
    yield f"http://localhost:{main_port}"
    for server in servers:
        server.terminate()
        server.wait()


def _wait_for_port(port: int) -> None:
    for _ in range(PORT_WAIT_ATTEMPTS):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(PORT_WAIT_SECONDS)  # a refused connection returns at once, so without this the wait lasts milliseconds
    raise RuntimeError(f"web server on port {port} did not start")


@contextlib.contextmanager
def scratch_home(home: Path) -> Iterator[Path]:
    """Fills home with uploads/resume.pdf (the only upload root), work/other.txt (outside it) and facts.md.
    A module that keeps one browser open for all its tests calls this from its own module-scoped fixture."""
    (home / "uploads").mkdir()
    (home / "uploads" / "resume.pdf").write_bytes(RESUME)
    (home / "work").mkdir()
    (home / "work" / "other.txt").write_text("not uploadable")
    (home / "facts.md").write_text(FACTS)
    try:
        yield home
    finally:
        shutil.rmtree(home / "profile", ignore_errors=True)  # a Chrome profile is tens of megabytes


@pytest.fixture(autouse=True)
def voice_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Every test gets its own empty voice cache, so fake audio never reaches ~/.beacon/voice."""
    monkeypatch.setattr(mouth, "VOICE_CACHE", tmp_path / "voice")
    return tmp_path / "voice"


@pytest.fixture
def front(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Stands in for the everyday browser, which a test would otherwise read from the developer's own screen.
    front.tab is what front_tab returns, or raises when it is an error; None, the start, is no Chrome in front."""
    front = SimpleNamespace(tab=None, profiles=[])

    async def front_tab(agent_profile: Path) -> tuple[str, str] | None:
        front.profiles.append(agent_profile)
        if isinstance(front.tab, Exception):
            raise front.tab
        return front.tab

    monkeypatch.setattr(everyday, "front_tab", front_tab)
    return front


@pytest.fixture
def folders(tmp_path: Path) -> Iterator[Path]:
    """A scratch home for one test: see scratch_home. Use folders / "profile" for a Chrome profile."""
    with scratch_home(tmp_path) as home:
        yield home
