"""The hardware boundary for tests. Importing this module replaces sounddevice.InputStream with a stream that plays
the clips queued in `pending`, in real time, over faint noise."""
import collections
import threading
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice

RATE = 16_000
INT16_FULL_SCALE = 32768
NOISE_AMPLITUDE = 1e-4  # a live microphone is never pure zeros; voice.check_microphone tells the two apart by that
NOISE_SEED = 0
pending: collections.deque[np.ndarray] = collections.deque()  # clips waiting to be spoken, oldest first
silent = [False]  # silent[0] = True imitates a microphone macOS is withholding; a list so importers can flip it


def clip(path: Path, seconds: float | None = None) -> np.ndarray:
    """The samples of a 16 kHz mono 16-bit wav file, cut to the first `seconds` when given."""
    with wave.open(str(path)) as file:
        if file.getframerate() != RATE:
            raise ValueError(f"{path} is {file.getframerate()} Hz, not {RATE}")
        audio = np.frombuffer(file.readframes(file.getnframes()), dtype=np.int16).astype(np.float32) / INT16_FULL_SCALE
    return audio if seconds is None else audio[:int(seconds * RATE)]


class FakeInputStream:
    """The parts of sounddevice.InputStream that Moonshine (callback form) and voice.check_microphone (read) use."""

    def __init__(self, samplerate, channels, dtype, blocksize=1024, device=None, callback=None):
        self._blocksize, self._callback = blocksize, callback
        self._speech = np.zeros(0, np.float32)  # what is left of the clip being spoken
        self._noise = np.random.default_rng(NOISE_SEED)
        self._closed = threading.Event()
        self.running = False  # like the real stream, it can be stopped and started again
        self._pump = threading.Thread(target=self._deliver_blocks, daemon=True)

    def __enter__(self) -> "FakeInputStream":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def read(self, frames: int) -> tuple[np.ndarray, bool]:
        return self._block(frames), False

    def start(self) -> None:
        self.running = True
        if not self._pump.is_alive():
            self._pump.start()

    def stop(self) -> None:
        self.running = False

    def close(self) -> None:
        self._closed.set()
        if self._pump.is_alive():
            self._pump.join()

    def _block(self, frames: int) -> np.ndarray:
        if len(self._speech) == 0 and pending:
            self._speech = pending.popleft()
        speech, self._speech = self._speech[:frames], self._speech[frames:]
        if silent[0]:
            block = np.zeros(frames, np.float32)
        else:
            block = self._noise.normal(0, NOISE_AMPLITUDE, frames).astype(np.float32)
        block[:len(speech)] += speech
        return block.reshape(-1, 1)

    def _deliver_blocks(self) -> None:
        due = time.perf_counter()
        while not self._closed.is_set():
            if self.running:
                self._callback(self._block(self._blocksize), self._blocksize, None, None)
            due += self._blocksize / RATE
            self._closed.wait(max(0, due - time.perf_counter()))


sounddevice.InputStream = FakeInputStream
