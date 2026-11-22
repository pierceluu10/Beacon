"""The menu bar mark: what Beacon is doing, the push-to-talk key, the wake word switch, memory, what it works in, on/off and
quit. A native helper (menubar.swift) shows it and holds the key; this file runs the assistant on a second thread, acts on
what the helper reports, and keeps the menu's choices in settings.json."""
import asyncio
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import main
from voice import Ears

SOURCE = Path(__file__).with_name("menubar.swift")
ICON = Path(__file__).with_name("mark.png")
HELPER = main.HOME / "menubar"
SETTINGS = main.HOME / "settings.json"
STARTING, OFF = "starting", "off"  # the other states are voice.Ears.activity; menubar.swift knows them all
STATE_CHECK_SECONDS = 0.15  # how quickly the icon follows what Beacon is doing
START_POLL_SECONDS = 0.1
QUIT = {"action": "quit"}
MODES = (main.MAC, main.CHROME, main.WINDOW)
DEFAULT_HOTKEY = {"code": 49, "modifiers": 2048, "label": "⌥Space"}  # Carbon's key code for Space and its Option mask
DEFAULT_SETTINGS = {"mode": main.MAC, "wake": True, "hotkey": DEFAULT_HOTKEY}


def load_settings(path: Path = SETTINGS) -> dict:
    saved = json.loads(path.read_text()) if path.exists() else {}
    return DEFAULT_SETTINGS | {key: value for key, value in saved.items() if key in DEFAULT_SETTINGS}


def save_settings(settings: dict, path: Path = SETTINGS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2, ensure_ascii=False))


def start_helper() -> subprocess.Popen:
    """Compile the helper when it is missing or older than its source, then start it."""
    if not HELPER.exists() or HELPER.stat().st_mtime < SOURCE.stat().st_mtime:
        HELPER.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["swiftc", "-O", str(SOURCE), "-o", str(HELPER)], check=True)
    return subprocess.Popen([str(HELPER), str(ICON), str(main.MEMORY_NOTES), str(main.MEMORY_FOLDER)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)


class Beacon:
    """The assistant on its own thread, switched by the menu's actions."""

    def __init__(self, settings: dict) -> None:
        self.settings = settings
        self._ears: Ears | None = None
        self._thread: threading.Thread | None = None

    @property
    def state(self) -> str:
        running = self._thread is not None and self._thread.is_alive()  # false after a crash; its reason is in the terminal
        return OFF if not running else STARTING if self._ears is None else self._ears.activity

    def start(self) -> None:
        self._ears = None
        self._loop = asyncio.new_event_loop()
        self._assistant = self._loop.create_task(main.main(self._started, self.settings["mode"], self.settings["wake"]))
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
        ears.set_wake(self.settings["wake"])  # in case it was switched while Beacon was starting
        self._ears = ears

    def _on_ears(self, method: str, *arguments: object) -> None:
        if self.state not in (OFF, STARTING):
            self._loop.call_soon_threadsafe(getattr(self._ears, method), *arguments)

    def act(self, message: dict) -> None:
        action = message["action"]
        if action == "push":
            self._on_ears("push", bool(message["down"]))
        elif action == "wake":
            self.settings["wake"] = not self.settings["wake"]
            save_settings(self.settings)
            self._on_ears("set_wake", self.settings["wake"])
        elif action == "hotkey":
            self.settings["hotkey"] = message["hotkey"]
            save_settings(self.settings)
        elif action == "power":
            self.stop() if self.state != OFF else self.start()
        elif action in MODES and action != self.settings["mode"]:
            self.settings["mode"] = action
            save_settings(self.settings)
            self.stop()
            self.start()


def run() -> None:
    config = main.load_config()
    beacon = Beacon(load_settings())
    helper = start_helper()
    events: queue.Queue[dict] = queue.Queue()

    def listen() -> None:
        for line in helper.stdout:
            events.put(json.loads(line))
        events.put(QUIT)  # the helper is gone

    threading.Thread(target=listen, daemon=True).start()
    beacon.start()
    shown = ""
    try:
        while True:
            status = json.dumps({"state": beacon.state, "mode": beacon.settings["mode"], "token": bool(config.extension_token),
                                 "wake": beacon.settings["wake"], "hotkey": beacon.settings["hotkey"],
                                 "name": config.agent_name}, ensure_ascii=False)
            if status != shown:
                helper.stdin.write(status + "\n")
                helper.stdin.flush()
                shown = status
            try:
                message = events.get(timeout=STATE_CHECK_SECONDS)
            except queue.Empty:
                continue
            if message == QUIT:
                break
            beacon.act(message)
    except (KeyboardInterrupt, BrokenPipeError):  # Ctrl-C in the terminal, or the helper died
        pass
    finally:
        beacon.stop()
        helper.terminate()
