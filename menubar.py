"""The menu bar icon: a Listening switch and Quit. AppKit owns the main thread, so the assistant runs on another."""
import asyncio
import signal
import sys
import threading

import rumps

import main
from voice import Ears

LISTENING_TITLE = "◉"
MUTED_TITLE = "○"
STARTING_TITLE = "…"
LISTENING_ITEM = "Listening"
QUIT_ITEM = "Quit Beacon"
ALIVE_CHECK_SECONDS = 1


class MenuBar(rumps.App):
    def __init__(self) -> None:
        super().__init__("Beacon", title=STARTING_TITLE, quit_button=None)
        self._switch = rumps.MenuItem(LISTENING_ITEM, callback=self._toggle)
        self.menu = [self._switch, rumps.MenuItem(QUIT_ITEM, callback=self._quit)]
        self._ears: Ears | None = None
        self._loop = asyncio.new_event_loop()
        self._assistant = self._loop.create_task(main.main(self._started))
        self._thread = threading.Thread(target=self._run_assistant)
        self._thread.start()
        signal.signal(signal.SIGINT, self._quit)  # Ctrl-C in the terminal; runs when the timer below wakes Python
        rumps.Timer(self._quit_when_assistant_ends, ALIVE_CHECK_SECONDS).start()

    def _run_assistant(self) -> None:
        try:
            self._loop.run_until_complete(self._assistant)
        except asyncio.CancelledError:
            pass
        except SystemExit as stopped:  # threads swallow SystemExit, and its code is the reason the user was promised
            print(stopped.code, file=sys.stderr)

    def _started(self, ears: Ears) -> None:  # on the assistant's thread; the timer shows it on the main thread
        self._ears = ears

    def _toggle(self, _: rumps.MenuItem) -> None:
        if self._ears is None:  # still starting up
            return
        self._switch.state = not self._switch.state
        self._loop.call_soon_threadsafe(self._ears.set_listening, bool(self._switch.state))
        self.title = LISTENING_TITLE if self._switch.state else MUTED_TITLE

    def _quit_when_assistant_ends(self, _: rumps.Timer) -> None:
        if self._ears is not None and self.title == STARTING_TITLE:
            self._switch.state, self.title = True, LISTENING_TITLE
        if not self._thread.is_alive():  # it crashed or exited; its own message is already in the terminal
            rumps.quit_application()

    def _quit(self, *_: object) -> None:
        self._loop.call_soon_threadsafe(self._assistant.cancel)
        self._thread.join()  # the browser and the microphone are closed before the icon goes
        rumps.quit_application()


def run() -> None:
    MenuBar().run()
