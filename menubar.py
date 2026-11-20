"""The menu bar bird: on/off, mute, which browser, quit. AppKit owns the main thread, so the assistant runs on another."""
import asyncio
import signal
import sys
import threading
from pathlib import Path

import AppKit
import rumps

import main
from voice import Ears

ICON = str(Path(__file__).with_name("bird.png"))
STARTING, LISTENING, MUTED, OFF = "Starting…", "Listening", "Muted", "Off"
TITLES = {STARTING: "…", LISTENING: None, MUTED: "muted", OFF: "off"}  # the short text beside the bird
MUTE_ITEM = "Mute"
TURN_OFF_ITEM, TURN_ON_ITEM = "Turn Off", "Turn On"
BROWSER_MENU = "Browser"
OWN_WINDOW_ITEM, MY_CHROME_ITEM = "Separate window", "My Chrome"
QUIT_ITEM = "Quit Beacon"
NO_TOKEN_TITLE = "My Chrome needs the Playwright Extension"
NO_TOKEN_TEXT = 'Install the extension, copy its token, and put extension_token = "..." in ~/.beacon/config.toml. See the README.'
STATE_CHECK_SECONDS = 1


class MenuBar(rumps.App):
    def __init__(self) -> None:
        super().__init__("Beacon", icon=ICON, template=True, quit_button=None)
        self._state = rumps.MenuItem(STARTING)  # no callback: shown greyed out, as a status line
        self._mute = rumps.MenuItem(MUTE_ITEM, callback=self._toggle_mute)
        self._power = rumps.MenuItem(TURN_OFF_ITEM, callback=self._toggle_power)
        self._own_window = rumps.MenuItem(OWN_WINDOW_ITEM, callback=self._use_own_window)
        self._my_chrome = rumps.MenuItem(MY_CHROME_ITEM, callback=self._use_my_chrome)
        self.menu = [self._state, None, self._mute, self._power, (BROWSER_MENU, [self._own_window, self._my_chrome]), None,
                     rumps.MenuItem(QUIT_ITEM, callback=self._quit)]
        self._has_token = bool(main.load_config().extension_token)
        self._in_my_chrome = self._has_token
        self._ears: Ears | None = None
        self._thread: threading.Thread | None = None
        self._start()
        signal.signal(signal.SIGINT, self._quit)  # Ctrl-C in the terminal; runs when the timer below wakes Python
        rumps.Timer(self._show_state, STATE_CHECK_SECONDS).start()

    def _start(self) -> None:
        self._ears = None
        self._loop = asyncio.new_event_loop()
        self._assistant = self._loop.create_task(main.main(self._started, self._in_my_chrome))
        self._thread = threading.Thread(target=self._run_assistant)
        self._thread.start()

    def _stop(self) -> None:
        if self._thread is not None:
            self._loop.call_soon_threadsafe(self._assistant.cancel)
            self._thread.join()  # the browser and the microphone are closed before anything else happens
            self._thread = None

    def _run_assistant(self) -> None:
        try:
            self._loop.run_until_complete(self._assistant)
        except asyncio.CancelledError:
            pass
        except SystemExit as stopped:  # threads swallow SystemExit, and its code is the reason the user was promised
            print(stopped.code, file=sys.stderr)
        finally:
            self._loop.close()

    def _started(self, ears: Ears) -> None:  # on the assistant's thread; the timer shows it on the main thread
        self._ears = ears

    def _show_state(self, _: rumps.Timer | None = None) -> None:
        running = self._thread is not None and self._thread.is_alive()  # false after a crash; its reason is in the terminal
        state = OFF if not running else STARTING if self._ears is None else MUTED if self._mute.state else LISTENING
        self._state.title, self.title = state, TITLES[state]
        self._power.title = TURN_ON_ITEM if state == OFF else TURN_OFF_ITEM
        self._own_window.state, self._my_chrome.state = not self._in_my_chrome, self._in_my_chrome

    def _toggle_mute(self, _: rumps.MenuItem) -> None:
        if self._ears is None:  # off or still starting
            return
        self._mute.state = not self._mute.state
        self._loop.call_soon_threadsafe(self._ears.set_listening, not self._mute.state)
        self._show_state()

    def _toggle_power(self, _: rumps.MenuItem) -> None:
        if self._power.title == TURN_ON_ITEM:
            self._restart()
        else:
            self._stop()
            self._show_state()

    def _restart(self) -> None:
        self._stop()
        self._mute.state = False
        self._start()
        self._show_state()

    def _use_own_window(self, _: rumps.MenuItem) -> None:
        if self._in_my_chrome:
            self._in_my_chrome = False
            self._restart()

    def _use_my_chrome(self, _: rumps.MenuItem) -> None:
        if not self._has_token:
            rumps.alert(NO_TOKEN_TITLE, NO_TOKEN_TEXT)
        elif not self._in_my_chrome:
            self._in_my_chrome = True
            self._restart()

    def _quit(self, *_: object) -> None:
        self._stop()
        rumps.quit_application()


def run() -> None:
    # macOS places a menu bar item only for a process that is an accessory app before the item is created.
    AppKit.NSApplication.sharedApplication().setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    MenuBar().run()
