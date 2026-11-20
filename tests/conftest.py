"""Shared fixtures: synthesized speech clips, two local web servers (the second gives a cross-origin iframe), scratch folders."""
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

SITE = Path(__file__).with_name("site")
CROSS_ORIGIN_PORT_PLACEHOLDER = "8772"
SAY_TIMEOUT_SECONDS = 10  # `say` occasionally hangs when writing a file; a killed run leaves a truncated clip
SAY_ATTEMPTS = 3
CLIPS = {
    "wake": "Hey Jarvis.",
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
    "slow": "Hey Jarvis, wait on this page until the text launch complete appears, then read me the heading.",
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
    for _ in range(100):
        with socket.socket() as probe:
            probe.settimeout(0.1)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
    raise RuntimeError(f"web server on port {port} did not start")


@pytest.fixture
def folders(tmp_path: Path) -> Path:
    """A scratch home: uploads/resume.pdf (the only upload root), work/other.txt (outside it), facts.md, profile/."""
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / "resume.pdf").write_bytes(RESUME)
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "other.txt").write_text("not uploadable")
    (tmp_path / "facts.md").write_text(FACTS)
    yield tmp_path
    shutil.rmtree(tmp_path / "profile", ignore_errors=True)  # a Chrome profile is tens of megabytes
