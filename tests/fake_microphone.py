"""The hardware boundary for tests: a sounddevice.InputStream that plays queued clips in real time over faint noise."""
import collections, threading, time, wave
from pathlib import Path
import numpy as np
import sounddevice

RATE = 16_000
pending: collections.deque = collections.deque()
silent = [False]  # True imitates a microphone macOS is withholding

def clip(path: Path, seconds: float | None = None) -> np.ndarray:
    with wave.open(str(path)) as file:
        assert file.getframerate() == RATE
        audio = np.frombuffer(file.readframes(file.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    return audio if seconds is None else audio[:int(seconds * RATE)]

class FakeInputStream:
    def __init__(self, samplerate, channels, dtype, blocksize=1024, device=None, callback=None):
        self.blocksize, self.callback, self.running = blocksize, callback, False
        self.buffer, self.noise = np.zeros(0, np.float32), np.random.default_rng(0)
    def block(self, frames):
        if len(self.buffer) == 0 and pending:
            self.buffer = pending.popleft()
        speech, self.buffer = self.buffer[:frames], self.buffer[frames:]
        out = np.zeros(frames, np.float32) if silent[0] else self.noise.normal(0, 1e-4, frames).astype(np.float32)
        out[:len(speech)] += speech
        return out.reshape(-1, 1)
    def read(self, frames): return self.block(frames), False
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def start(self):
        self.running = True; threading.Thread(target=self.pump, daemon=True).start()
    def pump(self):
        due = time.perf_counter()
        while self.running:
            self.callback(self.block(self.blocksize), self.blocksize, None, None)
            due += self.blocksize / RATE; time.sleep(max(0, due - time.perf_counter()))
    def stop(self): self.running = False
    close = stop

sounddevice.InputStream = FakeInputStream
