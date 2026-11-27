"""Local ears: Moonshine speech-to-text on the microphone, opened by the wake word, the push-to-talk key or a question,
and what the words mean. Speech goes out through mouth.Mouth."""
import asyncio
import contextlib
import re
from difflib import SequenceMatcher

import sounddevice
from moonshine_voice import MicTranscriber, ModelArch
from moonshine_voice.transcriber import LineCompleted, LineStarted, LineTextChanged, TranscriptEvent

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
SENTENCE_START = re.compile(r"^|[.?!]\s+")  # "5.07" has no space after its point, so it does not split a sentence
WAKE_SIMILARITY = 0.75
YES = ("yes", "yeah", "yep", "yup", "sure", "confirm", "correct", "go ahead", "do it", "submit")
YES_FILLERS = ("uh", "um", "okay", "ok", "please", "and")
YES_ONLY = rf"(({'|'.join(YES_FILLERS)}) )*({'|'.join(YES)})( ({'|'.join(YES + YES_FILLERS)}))*"
NO = ("no", "nope", "nah", "cancel", "stop", "don't", "do not", "wait", "hold on", "never mind")
STOP_WORD = "(stop|cancel|abort|never ?mind)"
STOP = rf"((no|please|okay|hey|wait) )*{STOP_WORD}( (it|that|this|please|right|now|everything|{STOP_WORD}))*"
DISMISS = (rf"{STOP}|(okay |great |no |hey )*(no|nope|thanks?( you)?( a lot| so much| very much)?"
           rf"|that('s| is) (all|it)|nothing( else)?|(good ?)?bye)")
# A line that stops on one of these, or on a comma, is cut off mid-clause: something must follow it.
TRAILING_WORDS = ("and", "or", "but", "so", "then", "to", "the", "a", "an", "my", "your", "of", "for", "with", "in", "on",
                  "at", "from", "by", "into", "about")
TRAILING_MARKS = (",", ":", ";", "...", "…")
UNFINISHED_PAUSE_SECONDS = 2.0  # how long such a line waits for the rest; a finished one is handed on at once
ECHO_OVERLAP = 0.6  # the share of a line heard while speaking that matches the spoken line, at which it is its echo
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
    """The words after the agent's name if a sentence opens with it ("" if nothing follows), else None. Every sentence
    is checked, not just the first: the recogniser runs other speech in the room into the same utterance."""
    for start in SENTENCE_START.finditer(transcript):
        sentence = transcript[start.end():]
        for index, word in enumerate(re.finditer(r"[\w']+", sentence)):
            if index == WAKE_WINDOW_WORDS:
                break
            if _is_name(word[0], names):
                return sentence[word.end():].strip(" ,.!?")
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


def is_unfinished(transcript: str) -> bool:
    """True when a line stops mid-clause, on a comma or a word such as "and" or "to"; a closing period is ignored, since
    the recogniser may put one at any pause."""
    words = _plain(transcript).split()
    return transcript.rstrip().endswith(TRAILING_MARKS) or bool(words) and words[-1] in TRAILING_WORDS


def is_echo(transcript: str, spoken: str) -> bool:
    """True when most of a line heard while Beacon speaks is the spoken line itself, coming back through the microphone:
    macOS gives sounddevice no echo cancelling."""
    heard = _plain(transcript).split()
    matched = sum(block.size for block in SequenceMatcher(None, heard, _plain(spoken).split()).get_matching_blocks())
    return not heard or matched >= ECHO_OVERLAP * len(heard)


def barges_in(transcript: str, spoken: str, names: tuple[str, ...]) -> bool:
    """A line heard while Beacon speaks cuts it off only when it wakes or stops it and is not its own words."""
    wakes_or_stops = wake_command(transcript, names) is not None or is_stop(transcript, names)
    return wakes_or_stops and not is_echo(transcript, spoken)


def triggers_in(spoken: str, names: tuple[str, ...]) -> bool:
    """True when a line Beacon says has a name or a stop word in it, which its echo could be mistaken for."""
    words = re.findall(r"[a-z']+", spoken.lower())
    return any(_is_name(word, names) for word in words) or bool(re.search(STOP_WORD, " ".join(words)))


class Heard(str):
    """An utterance; pushed is True when it was spoken with the push-to-talk key held, so it is meant for Beacon."""

    pushed: bool

    def __new__(cls, text: str, pushed: bool = False) -> "Heard":
        heard = super().__new__(cls, text)
        heard.pushed = pushed
        return heard


class Ears:
    """Microphone to finished utterances, and speech out. While Beacon speaks the microphone stays open, but only a wake
    or a stop that is not its own echo gets through: it cuts the speech off and goes to the next listener.

    The microphone is open while the wake word is on, while the push-to-talk key is held, and while someone waits for
    an answer with a deadline (a question, the follow-up window). Otherwise it is closed and macOS shows it unused."""

    def __init__(self, stt_model: str, keyterms: list[str], mouth: Mouth, wake: bool) -> None:
        self._keyterms, self._mouth, self._wake = keyterms, mouth, wake
        self._names = tuple(keyterms)
        self.working = False  # set by the conversation while a task runs; only shown in the menu bar
        self._waiters: list[asyncio.Future[Heard]] = []
        self._expecting = 0  # waiters with a deadline: Beacon expects an answer, so the microphone opens for it
        self._quiet = asyncio.Event()  # cleared while someone is in the middle of a phrase
        self._quiet.set()
        self._voice = asyncio.Lock()  # one spoken line at a time
        self._speech: asyncio.Task | None = None  # the line being spoken, which the push-to-talk key cuts off
        self._later: set[asyncio.Task] = set()  # lines queued by say_later
        self._talking = False
        self._spoken = ""  # the line being spoken, to tell its echo from the user
        self._barged: Heard | None = None  # a barge-in nobody was listening for yet, kept for the next hear
        self._cut_early = False  # a line still being said cut Beacon off; its whole text is delivered when it ends
        self._unfinished = ""  # a line that stopped mid-clause, waiting for the rest
        self._unfinished_timer: asyncio.TimerHandle | None = None
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
        self._flush()  # what came before the press stays apart
        self._pushing = down
        if down:
            self._barged = None
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

    def _flush(self) -> None:
        """Deliver the phrase in progress, then any line still held for its rest, as they are."""
        if self._running:
            self._mic.stop()
            self._mic.start()
        self._loop.call_soon(self._hand_unfinished)  # after the lines the stop above delivered

    def _want_microphone(self) -> bool:
        return self._wake or self._pushing or self._expecting > 0

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
            self._loop.call_soon_threadsafe(self._line_started)
        elif isinstance(event, LineTextChanged) and self._talking:
            self._loop.call_soon_threadsafe(self._partial, event.line.text.strip())
        elif isinstance(event, LineCompleted):
            self._loop.call_soon_threadsafe(self._deliver, event.line.text.strip(), self._pushing, self._talking)

    def _line_started(self) -> None:
        self._quiet.clear()
        if self._unfinished_timer:  # the rest is coming
            self._unfinished_timer.cancel()
            self._unfinished_timer = None

    def _partial(self, text: str) -> None:
        """The name or a stop cuts Beacon off as soon as it is heard, not once the user has finished the sentence. A
        transcript in progress is a rough guess, too rough to tell from Beacon's own echo, so while Beacon says the name
        or a stop word itself, the finished transcript decides instead."""
        if self._talking and not triggers_in(self._spoken, self._names) and barges_in(text, self._spoken, self._names):
            self._cut_early = True
            self.hush()

    def _deliver(self, text: str, pushed: bool, talking: bool) -> None:
        if pushed:
            self._held.append(text)
            return
        self._lines_kept += 1
        if self._lines_kept >= RESTART_AFTER_LINES and self._running and not self._pushing:
            # Hours of background sound would otherwise grow memory and processor use without limit.
            self._lines_kept = 0
            self._mic.stop()
            self._mic.start()
        if talking:
            self._quiet.set()
            self._cut_early = False
            if barges_in(text, self._spoken, self._names):
                self.hush()
                if not self._hand(Heard(text)):
                    self._barged = Heard(text)
            return
        text = f"{self._unfinished} {text}".strip()
        self._unfinished = ""
        if is_unfinished(text) and not self._talking and not self._pushing:
            self._unfinished = text  # the quiet stays cleared, so hear and say wait for the rest
            self._unfinished_timer = self._loop.call_later(UNFINISHED_PAUSE_SECONDS, self._hand_unfinished)
            return
        self._quiet.set()
        if not self._hand(Heard(text)) and self._cut_early:
            self._barged = Heard(text)
        self._cut_early = False

    def _hand_unfinished(self) -> None:
        if self._unfinished_timer:
            self._unfinished_timer.cancel()
            self._unfinished_timer = None
        if self._unfinished:
            text, self._unfinished = self._unfinished, ""
            self._quiet.set()
            self._hand(Heard(text))

    def _release(self) -> None:
        if not self._pushing:  # pressed again before this ran: those lines belong to the new push
            self._quiet.set()
            self._hand(Heard(" ".join(filter(None, self._held)), pushed=True))

    def _hand(self, heard: Heard) -> bool:
        # The newest listener wins, so a question asked mid-task outranks the stop watcher.
        for waiter in reversed(self._waiters):
            if heard and not waiter.done():
                waiter.set_result(heard)
                return True
        return False

    async def hear(self, timeout: float | None = None) -> Heard | None:
        """The next finished utterance, or None if nobody starts speaking within timeout seconds. A deadline means
        Beacon expects an answer, so the microphone opens for it even with the wake word off. A barge-in that cut off a
        line while nobody was listening comes first."""
        if self._barged:
            heard, self._barged = self._barged, None
            return heard
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
        """Speak one line. The push-to-talk key, the wake word or a stop cuts it off; then say returns early."""
        async with self._voice:
            with contextlib.suppress(TimeoutError):  # still talking after the wait: speak anyway, unless the key is held
                await asyncio.wait_for(self._quiet.wait(), None if self._pushing else QUIET_WAIT_SECONDS)
            # Flushing delivers the phrase in progress to whoever is listening now, so it cannot be stitched onto the
            # answer to this line. Measured 4-100 ms, so it runs on the loop: a cancelled thread could not be undone.
            self._flush()
            self._talking, self._spoken = True, text
            speech = self._speech = asyncio.ensure_future(self._mouth.speak(text))
            try:
                await asyncio.wait({speech})
                if not speech.cancelled():
                    speech.result()
                    await asyncio.sleep(ECHO_TAIL_SECONDS)
            finally:
                speech.cancel()
                await asyncio.wait({speech})  # lets the speakers or `say` close
                # The echo still in the recogniser is delivered as heard while talking, so it is filtered; but after an
                # early cut-off the line in progress is the user's, and a restart would split it.
                if self._running and not self._cut_early:
                    self._mic.stop()
                    self._mic.start()
                self._speech = None
                self._talking = False
                if not self._cut_early:  # otherwise the user is mid-line, and its end sets the quiet
                    self._quiet.set()

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
