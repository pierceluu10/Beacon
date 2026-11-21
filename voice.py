"""Local ears and mouth: Moonshine speech-to-text on the microphone, macOS `say` for speech, and what the words mean."""
import asyncio
import contextlib
import re
from difflib import SequenceMatcher

import sounddevice
from moonshine_voice import MicTranscriber, ModelArch
from moonshine_voice.transcriber import LineCompleted, LineStarted, TranscriptEvent

SAY = "/usr/bin/say"
SAY_WORDS_PER_MINUTE = "190"
SAY_TIMEOUT_SECONDS = 20  # `say` hung once in testing here; prompt.txt holds an answer to 35 words, about 13 s
ECHO_TAIL_SECONDS = 0.3
QUIET_WAIT_SECONDS = 3  # how long a spoken line waits for the user to finish a sentence
MAX_UTTERANCE_SECONDS = 15
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


async def speak(text: str, voice: str) -> None:
    """Speak one line through the speakers. Cancelling the caller cuts it off within milliseconds."""
    process = await asyncio.create_subprocess_exec(SAY, "-v", voice, "-r", SAY_WORDS_PER_MINUTE, "--", text)
    try:
        with contextlib.suppress(TimeoutError):  # a hung `say` is killed below and the conversation goes on
            await asyncio.wait_for(process.wait(), SAY_TIMEOUT_SECONDS)
    finally:
        if process.returncode is None:
            process.terminate()
            await process.wait()


class Ears:
    """Microphone to finished utterances, and speech out with the microphone paused (half-duplex)."""

    def __init__(self, stt_model: str, keyterms: list[str], say_voice: str) -> None:
        self._keyterms, self._say_voice = keyterms, say_voice
        self._waiters: list[asyncio.Future[str]] = []
        self._quiet = asyncio.Event()  # cleared while someone is in the middle of a phrase
        self._quiet.set()
        self._voice = asyncio.Lock()  # one spoken line at a time
        self._listening = True  # the menu bar switch; off means the microphone stays stopped
        self._lines_kept = 0
        self._mic = MicTranscriber().model_arch(ModelArch[stt_model])
        self._mic.add_listener(self._on_event)

    async def __aenter__(self) -> "Ears":
        self._loop = asyncio.get_running_loop()
        await asyncio.to_thread(self._mic.load)  # downloads the model on first run
        self._mic.set_keyterms(self._keyterms)
        await asyncio.to_thread(check_microphone)
        self._mic.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self._mic.close()

    def _on_event(self, event: TranscriptEvent) -> None:  # transcriber thread, or this loop's during stop()
        if isinstance(event, LineStarted):
            self._loop.call_soon_threadsafe(self._quiet.clear)
        elif isinstance(event, LineCompleted):
            self._loop.call_soon_threadsafe(self._deliver, event.line.text.strip())

    def _deliver(self, text: str) -> None:
        self._quiet.set()
        self._lines_kept += 1
        if self._lines_kept >= RESTART_AFTER_LINES and self._listening and not self._voice.locked():
            # Hours of background sound would otherwise grow memory and processor use without limit.
            self._lines_kept = 0
            self._mic.stop()
            self._mic.start()
        # The newest listener wins, so a question asked mid-task outranks the stop watcher.
        for waiter in reversed(self._waiters):
            if text and not waiter.done():
                waiter.set_result(text)
                return

    async def hear(self, timeout: float | None = None) -> str | None:
        """The next finished utterance, or None if nobody starts speaking within timeout seconds."""
        waiter = self._loop.create_future()
        self._waiters.append(waiter)
        try:
            await asyncio.wait({waiter}, timeout=timeout)
            if not waiter.done() and not self._quiet.is_set():  # they began before the deadline: let them finish
                with contextlib.suppress(TimeoutError):  # _deliver ends the quiet wait and fills the waiter together
                    await asyncio.wait_for(self._quiet.wait(), MAX_UTTERANCE_SECONDS)
            return waiter.result() if waiter.done() else None
        finally:
            self._waiters.remove(waiter)

    async def say(self, text: str) -> None:
        async with self._voice:
            with contextlib.suppress(TimeoutError):  # still talking after the wait: speak anyway
                await asyncio.wait_for(self._quiet.wait(), QUIET_WAIT_SECONDS)
            # Stopping flushes the phrase in progress to whoever is listening now, so it cannot be stitched onto the
            # answer to this line. Measured 4-100 ms, so it runs on the loop: a cancelled thread could not be undone.
            self._mic.stop()
            self._lines_kept = 0
            try:
                await speak(text, self._say_voice)
                await asyncio.sleep(ECHO_TAIL_SECONDS)
            finally:
                self._quiet.set()
                if self._listening:
                    self._mic.start()

    def set_listening(self, listening: bool) -> None:
        """Switch the microphone on or off. Call on the event loop; a line being spoken finishes first."""
        self._listening = listening
        # The recogniser's own stop keeps the audio device open, and macOS then still shows the microphone as in use.
        device = self._mic._sd_stream
        if listening:
            device.start()
        if not self._voice.locked():  # say() restarts the recogniser itself when it is done
            (self._mic.start if listening else self._mic.stop)()
        if not listening:
            device.stop()
