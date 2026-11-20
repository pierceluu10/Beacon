"""The menu bar bird: on/off, mute, which browser, quit. A native helper (menubar.swift) shows it; this file runs the
assistant on a second thread and acts on the helper's clicks."""
import asyncio
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import main
from voice import Ears

SOURCE = Path(__file__).with_name("menubar.swift")
ICON = Path(__file__).with_name("bird.png")
HELPER = main.HOME / "menubar"
STARTING, LISTENING, MUTED, OFF = "Starting…", "Listening", "Muted", "Off"  # menubar.swift knows these four
STATE_CHECK_SECONDS = 1
START_POLL_SECONDS = 0.1
QUIT = "quit"


def start_helper() -> subprocess.Popen:
    """Compile the helper when it is missing or older than its source, then start it."""
    if not HELPER.exists() or HELPER.stat().st_mtime < SOURCE.stat().st_mtime:
        HELPER.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["swiftc", "-O", str(SOURCE), "-o", str(HELPER)], check=True)
    return subprocess.Popen([str(HELPER), str(ICON)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)


class Beacon:
    """The assistant on its own thread, switched by the menu's actions."""

    def __init__(self, in_my_chrome: bool) -> None:
        self.in_my_chrome = in_my_chrome
        self._muted = False
        self._ears: Ears | None = None
        self._thread: threading.Thread | None = None

    @property
    def state(self) -> str:
        running = self._thread is not None and self._thread.is_alive()  # false after a crash; its reason is in the terminal
        return OFF if not running else STARTING if self._ears is None else MUTED if self._muted else LISTENING

    def start(self) -> None:
        self._ears, self._muted = None, False
        self._loop = asyncio.new_event_loop()
        self._assistant = self._loop.create_task(main.main(self._started, self.in_my_chrome))
        self._thread = threading.Thread(target=self._run)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is not None:
            while self.state == STARTING:  # cancelling the microphone check halfway deadlocks inside CoreAudio
                time.sleep(START_POLL_SECONDS)
            self._loop.call_soon_threadsafe(self._assistant.cancel)
            self._thread.join()  # the browser and the microphone are closed before anything else happens
            self._thread = None

    def _run(self) -> None:
        try:
            self._loop.run_until_complete(self._assistant)
        except asyncio.CancelledError:
            pass
        except SystemExit as stopped:  # threads swallow SystemExit, and its code is the reason the user was promised
            print(stopped.code, file=sys.stderr)
        finally:
            self._loop.close()

    def _started(self, ears: Ears) -> None:  # called on the assistant's thread
        self._ears = ears

    def act(self, action: str) -> None:
        if action == "mute" and self.state in (LISTENING, MUTED):
            self._muted = not self._muted
            self._loop.call_soon_threadsafe(self._ears.set_listening, not self._muted)
        elif action == "power":
            self.stop() if self.state != OFF else self.start()
        elif action in ("own_window", "my_chrome") and self.in_my_chrome != (action == "my_chrome"):
            self.in_my_chrome = action == "my_chrome"
            self.stop()
            self.start()


def run() -> None:
    has_token = bool(main.load_config().extension_token)
    beacon = Beacon(in_my_chrome=has_token)
    helper = start_helper()
    clicks: queue.Queue[str] = queue.Queue()

    def listen() -> None:
        for line in helper.stdout:
            clicks.put(line.strip())
        clicks.put(QUIT)  # the helper is gone

    threading.Thread(target=listen, daemon=True).start()
    beacon.start()
    try:
        while True:
            helper.stdin.write(f"{beacon.state}\t{int(beacon.in_my_chrome)}\t{int(has_token)}\n")
            helper.stdin.flush()
            try:
                action = clicks.get(timeout=STATE_CHECK_SECONDS)
            except queue.Empty:
                continue
            if action == QUIT:
                break
            beacon.act(action)
    except (KeyboardInterrupt, BrokenPipeError):  # Ctrl-C in the terminal, or the helper died
        pass
    finally:
        beacon.stop()
        helper.terminate()
