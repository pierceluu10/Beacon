"""Local ears: Moonshine speech-to-text on the microphone, opened by the wake word, the push-to-talk key or a question,
and what the words mean. Speech goes out through mouth.Mouth."""
import asyncio
import contextlib
import re
from difflib import SequenceMatcher

import sounddevice
from moonshine_voice import MicTranscriber, ModelArch
from moonshine_voice.transcriber import LineCompleted, LineStarted, TranscriptEvent

from mouth import Mouth, chime

ECHO_TAIL_SECONDS = 0.3
QUIET_WAIT_SECONDS = 3  # how long a spoken line waits for the user to finish a sentence
MAX_UTTERANCE_SECONDS = 15
MICROPHONE_LINGER_SECONDS = 0.5  # back-to-back listening windows keep the microphone open instead of cutting a phrase
RESTART_AFTER_LINES = 8  # the recogniser keeps every line's audio and re-reads all of it on each update
SAMPLE_RATE = 16_000
MICROPHONE_CHECK_FRAMES = SAMPLE_RATE // 2
MICROPHONE_CHECK_ATTEMPTS = 30  # 15 s: time to answer the macOS permission prompt on the first run
WAKE_WINDOW_WORDS = 3
WAKE_SIMILARITY = 0.75
YES = ("yes", "yeah", "yep", "yup", "sure", "confirm", "correct", "go ahead", "do it", "submit")
YES_FILLERS = ("uh", "um", "okay", "ok", "please", "and")
YES_ONLY = rf"(({'|'.join(YES_FILLERS)}) )*({'|'.join(YES)})( ({'|'.join(YES + YES_FILLERS)}))*"
NO = ("no", "nope", "nah", "cancel", "stop", "don't", "do not", "wait", "hold on", "never mind")
STOP_WORD = "(stop|cancel|abort|never ?mind)"
STOP = rf"((no|please|okay|hey|wait) )*{STOP_WORD}( (it|that|this|please|right|now|everything|{STOP_WORD}))*"
DISMISS = (rf"{STOP}|(okay |great |no |hey )*(no|nope|thanks?( you)?( a lot| so much| very much)?"
           rf"|that('s| is) (all|it)|nothing( else)?|(good ?)?bye)")
READY, HEARING, WORKING, SPEAKING = "ready", "hearing", "working", "speaking"  # Ears.activity, shown in the menu bar


class MicrophoneSilentError(Exception):
    """The microphone delivers pure digital silence: macOS is withholding it, or the input is muted."""


def check_microphone() -> None:
    for _ in range(MICROPHONE_CHECK_ATTEMPTS):
        with sounddevice.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32") as probe:
            samples, _ = probe.read(MICROPHONE_CHECK_FRAMES)
        if samples.any():
            return
    raise MicrophoneSilentError


def _is_name(word: str, names: tuple[str, ...]) -> bool:
    return any(SequenceMatcher(None, word.lower(), name.lower()).ratio() >= WAKE_SIMILARITY for name in names)


def _plain(transcript: str, names: tuple[str, ...] = ()) -> str:
    """The words in lower case, minus the agent's name: it may be said anywhere in a stop or a closing remark."""
    return " ".join(word for word in re.findall(r"[a-z']+", transcript.lower()) if not _is_name(word, names))


def _mentions(transcript: str, phrases: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(phrase)}\b", _plain(transcript)) for phrase in phrases)


def wake_command(transcript: str, names: tuple[str, ...]) -> str | None:
    """The words after the agent's name if the utterance opens with it ("" if nothing follows), else None."""
    for index, word in enumerate(re.finditer(r"[\w']+", transcript)):
        if index == WAKE_WINDOW_WORDS:
            break
        if _is_name(word[0], names):
            return transcript[word.end():].strip(" ,.!?")
    return None


def parse_yes_no(transcript: str) -> bool | None:
    """True for an answer of yes words only, False for no (no wins when both are heard), None when unclear."""
    if _mentions(transcript, NO):
        return False
    return True if re.fullmatch(YES_ONLY, _plain(transcript)) else None


def is_stop(transcript: str, names: tuple[str, ...] = ()) -> bool:
    return re.fullmatch(STOP, _plain(transcript, names)) is not None


def is_dismissal(transcript: str, names: tuple[str, ...] = ()) -> bool:
    return re.fullmatch(DISMISS, _plain(transcript, names)) is not None


class Heard(str):
    """An utterance; pushed is True when it was spoken with the push-to-talk key held, so it is meant for Beacon."""

    pushed: bool

    def __new__(cls, text: str, pushed: bool = False) -> "Heard":
        heard = super().__new__(cls, text)
        heard.pushed = pushed
        return heard


class Ears:
    """Microphone to finished utterances, and speech out with the microphone paused (half-duplex).

    The microphone is open while the wake word is on, while the push-to-talk key is held, and while someone waits for
    an answer with a deadline (a question, the follow-up window). Otherwise it is closed and macOS shows it unused."""

    def __init__(self, stt_model: str, keyterms: list[str], mouth: Mouth, wake: bool) -> None:
        self._keyterms, self._mouth, self._wake = keyterms, mouth, wake
        self.working = False  # set by the conversation while a task runs; only shown in the menu bar
        self._waiters: list[asyncio.Future[Heard]] = []
        self._expecting = 0  # waiters with a deadline: Beacon expects an answer, so the microphone opens for it
        self._quiet = asyncio.Event()  # cleared while someone is in the middle of a phrase
        self._quiet.set()
        self._voice = asyncio.Lock()  # one spoken line at a time
        self._speech: asyncio.Task | None = None  # the line being spoken, which the push-to-talk key cuts off
        self._later: set[asyncio.Task] = set()  # lines queued by say_later
        self._talking = False
        self._pushing = False
        self._held: list[str] = []  # the lines of the current push, delivered together on release
        self._running = False  # the recogniser and the audio device, which are opened and closed together
        self._closing: asyncio.TimerHandle | None = None
        self._lines_kept = 0
        self._mic = MicTranscriber().model_arch(ModelArch[stt_model])
        self._mic.add_listener(self._on_event)

    async def __aenter__(self) -> "Ears":
        self._loop = asyncio.get_running_loop()
        await asyncio.to_thread(self._mic.load)  # downloads the model on first run
        self._mic.set_keyterms(self._keyterms)
        await asyncio.to_thread(check_microphone)
        self._mic.start()  # the first start opens the audio device; from then on it is started and stopped here
        self._running = True
        self._refresh()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self.hush()
        self._mic.close()

    @property
    def activity(self) -> str:
        if self._talking:
            return SPEAKING
        if self._pushing or self._expecting and self._running:
            return HEARING
        return WORKING if self.working else READY

    @property
    def wake(self) -> bool:
        return self._wake

    def set_wake(self, wake: bool) -> None:
        """Listen for the agent's name between commands, or only open the microphone when there is a reason to."""
        self._wake = wake
        self._refresh()

    def push(self, down: bool) -> None:
        """The push-to-talk key. Pressing it cuts off Beacon's speech and opens the microphone; releasing it delivers
        everything said meanwhile as one utterance."""
        if down == self._pushing:
            return  # key repeat
        if self._running:  # stopping flushes the phrase in progress, so what came before the press stays apart
            self._mic.stop()
            self._mic.start()
        self._pushing = down
        if down:
            self.hush()
            self._held.clear()
            self._quiet.clear()
        else:
            self._loop.call_soon(self._release)  # after the lines the flush above delivered
        self._refresh()

    def hush(self) -> None:
        """Stop talking now: the line being spoken and any queued by say_later."""
        for task in [*self._later, *filter(None, [self._speech])]:
            task.cancel()

    def _want_microphone(self) -> bool:
        return not self._talking and (self._wake or self._pushing or self._expecting > 0)

    def _refresh(self, linger: bool = False) -> None:
        """Open or close the microphone to match what is wanted now. With linger, closing waits a moment, so a window
        that ends just before the next one begins does not cut the phrase in between."""
        if self._closing:
            self._closing.cancel()
            self._closing = None
        if self._want_microphone() == self._running:
            return
        if self._running and linger:
            self._closing = self._loop.call_later(MICROPHONE_LINGER_SECONDS, self._refresh)
            return
        # The recogniser's own stop keeps the audio device open, and macOS then still shows the microphone as in use.
        device = self._mic._sd_stream
        if self._running:
            self._mic.stop()  # flushes the phrase in progress to whoever is listening now
            device.stop()
        else:
            device.start()
            self._mic.start()
        self._running = not self._running
        self._lines_kept = 0

    def _on_event(self, event: TranscriptEvent) -> None:  # transcriber thread, or this loop's during stop()
        if isinstance(event, LineStarted):
            self._loop.call_soon_threadsafe(self._quiet.clear)
        elif isinstance(event, LineCompleted):
            self._loop.call_soon_threadsafe(self._deliver, event.line.text.strip(), self._pushing)

    def _deliver(self, text: str, pushed: bool) -> None:
        if pushed:
            self._held.append(text)
            return
        self._quiet.set()
        self._lines_kept += 1
        if self._lines_kept >= RESTART_AFTER_LINES and self._running and not self._pushing:
            # Hours of background sound would otherwise grow memory and processor use without limit.
            self._lines_kept = 0
            self._mic.stop()
            self._mic.start()
        self._hand(Heard(text))

    def _release(self) -> None:
        if not self._pushing:  # pressed again before this ran: those lines belong to the new push
            self._quiet.set()
            self._hand(Heard(" ".join(filter(None, self._held)), pushed=True))

    def _hand(self, heard: Heard) -> None:
        # The newest listener wins, so a question asked mid-task outranks the stop watcher.
        for waiter in reversed(self._waiters):
            if heard and not waiter.done():
                waiter.set_result(heard)
                return

    async def hear(self, timeout: float | None = None) -> Heard | None:
        """The next finished utterance, or None if nobody starts speaking within timeout seconds. A deadline means
        Beacon expects an answer, so the microphone opens for it even with the wake word off."""
        waiter = self._loop.create_future()
        self._waiters.append(waiter)
        if timeout is not None:
            self._expecting += 1
            self._refresh()
        try:
            await asyncio.wait({waiter}, timeout=timeout)
            if not waiter.done() and not self._quiet.is_set():  # they began before the deadline: let them finish
                with contextlib.suppress(TimeoutError):  # _deliver ends the quiet wait and fills the waiter together
                    await asyncio.wait_for(self._quiet.wait(), MAX_UTTERANCE_SECONDS)
            return waiter.result() if waiter.done() else None
        finally:
            self._waiters.remove(waiter)
            if timeout is not None:
                self._expecting -= 1
                self._refresh(linger=True)

    async def say(self, text: str) -> None:
        """Speak one line with the microphone closed. The push-to-talk key cuts it off; then say returns early."""
        async with self._voice:
            with contextlib.suppress(TimeoutError):  # still talking after the wait: speak anyway, unless the key is held
                await asyncio.wait_for(self._quiet.wait(), None if self._pushing else QUIET_WAIT_SECONDS)
            # Closing the microphone flushes the phrase in progress to whoever is listening now, so it cannot be stitched
            # onto the answer to this line. Measured 4-100 ms, so it runs on the loop: a cancelled thread could not be undone.
            self._talking = True
            self._refresh()
            speech = self._speech = asyncio.ensure_future(self._mouth.speak(text))
            try:
                await asyncio.wait({speech})
                if not speech.cancelled():
                    speech.result()
                    await asyncio.sleep(ECHO_TAIL_SECONDS)
            finally:
                speech.cancel()
                await asyncio.wait({speech})  # lets the speakers or `say` close before the microphone reopens
                self._speech = None
                self._talking = False
                self._quiet.set()
                self._refresh()

    def say_later(self, text: str) -> None:
        """Speak a line without waiting for it, after anything already being said; hush drops it."""
        task = asyncio.ensure_future(self.say(text))
        self._later.add(task)
        task.add_done_callback(self._later.discard)

    def chime(self) -> None:
        """The cue that Beacon heard its name alone and is listening for the command."""
        task = asyncio.ensure_future(chime())
        self._later.add(task)
        task.add_done_callback(self._later.discard)
