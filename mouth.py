"""Speech out: ElevenLabs streamed straight to the speakers, with your own ELEVENLABS_API_KEY or through Beacon's server,
and macOS `say` when neither answers."""
import asyncio
import contextlib
import hashlib
import os
import sys
import time
from collections.abc import AsyncIterator, Iterable
from pathlib import Path

import httpx2
import sounddevice

from credit import SERVER, device_id
from timing import span

SAY = "/usr/bin/say"
SAY_VOICE = "Samantha"
SAY_WORDS_PER_MINUTE = "190"
SAY_TIMEOUT_SECONDS = 20  # `say` hung once in testing here; the prompts hold an answer to 35 words, about 13 s
CHIME = "/System/Library/Sounds/Tink.aiff"
AFPLAY = "/usr/bin/afplay"
ELEVENLABS_STREAM = "https://api.elevenlabs.io/v1/text-to-speech/{voice}/stream"
HOSTED_STREAM = f"{SERVER}/v1/text-to-speech/{{voice}}/stream"
ELEVENLABS_KEY = "ELEVENLABS_API_KEY"
ELEVENLABS_VOICE = "kQWyq6XfiOkDFCzENZHW"  # "Vance"
ELEVENLABS_MODEL = "eleven_v4_turbo"
OUTPUT_FORMAT = "pcm_24000"  # raw 16-bit mono: playable as it arrives, and available on every plan
OUTPUT_RATE = 24_000
SAMPLE_BYTES = 2
# Stopping the stream right after the last sample makes the speakers crackle; measured on a MacBook Pro, 0.25 s of
# silence first still crackled sometimes, 0.5 s never did.
QUIET_TAIL = bytes(OUTPUT_RATE // 2 * SAMPLE_BYTES)
SPEECH_TIMEOUT = httpx2.Timeout(10, connect=5)
# How everything Beacon says should sound; every prompt that writes words to be spoken includes it.
SPEAKING_STYLE = ("Sound like a relaxed, friendly friend helping out, not an assistant reading a script: everyday words, "
                  "contractions, and short sentences the way people actually say them out loud. Skip formal phrasing and "
                  "filler, and don't repeat back what the user already knows.")
# Below ElevenLabs' default of 0.5 for a livelier, less monotone delivery; it suggests 0.3 to 0.5 for conversation.
ELEVENLABS_STABILITY = 0.4
REJECTED = (401, 402, 403)
VOICE_CACHE = Path("~/.beacon/voice").expanduser()
# Short lines repeat (acknowledgements, narration, errors) and play from disk at once; long answers rarely do.
CACHED_CHARACTERS = 60
CACHED_CLIPS = 400  # at most about 4 s of audio each: under 80 MB  # bad key, no credit left, or the voice is not on the account: no point retrying each line


def elevenlabs_request(http: httpx2.AsyncClient, key: str, text: str):
    """With a key, straight to ElevenLabs; without one, through Beacon's server, which adds its own key."""
    url, headers = (ELEVENLABS_STREAM, {"xi-api-key": key}) if key else (HOSTED_STREAM, {"x-api-key": device_id()})
    return http.stream("POST", url.format(voice=ELEVENLABS_VOICE), params={"output_format": OUTPUT_FORMAT},
                       headers=headers, json={"text": text, "model_id": ELEVENLABS_MODEL,
                                              "voice_settings": {"stability": ELEVENLABS_STABILITY}})


def clip_path(text: str) -> Path:
    """Where a line's audio is kept: anything that changes how it sounds is part of the name."""
    spoken = f"{ELEVENLABS_VOICE}|{ELEVENLABS_MODEL}|{OUTPUT_FORMAT}|{ELEVENLABS_STABILITY}|{text}"
    return VOICE_CACHE / f"{hashlib.sha256(spoken.encode()).hexdigest()}.pcm"


def keep_clip(path: Path, audio: bytes) -> None:
    """Save a clip whole or not at all, then drop the least recently played beyond CACHED_CLIPS."""
    VOICE_CACHE.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(".part")
    part.write_bytes(audio)
    part.replace(path)
    clips = sorted(VOICE_CACHE.glob("*.pcm"), key=lambda clip: clip.stat().st_mtime)
    for stale in clips[:-CACHED_CLIPS]:
        stale.unlink(missing_ok=True)


async def say(text: str, voice: str) -> None:
    """Speak one line with macOS `say`. Cancelling the caller cuts it off within milliseconds."""
    process = await asyncio.create_subprocess_exec(SAY, "-v", voice, "-r", SAY_WORDS_PER_MINUTE, "--", text)
    try:
        with contextlib.suppress(TimeoutError):  # a hung `say` is killed below and the conversation goes on
            await asyncio.wait_for(process.wait(), SAY_TIMEOUT_SECONDS)
    finally:
        if process.returncode is None:
            process.terminate()
            await process.wait()


async def chime() -> None:
    """The short sound for "I'm listening" after the name alone."""
    process = await asyncio.create_subprocess_exec(AFPLAY, CHIME)
    await process.wait()


class Mouth:
    """One voice for everything Beacon says. Use as an async context manager."""

    def __init__(self) -> None:
        self._key = os.environ.get(ELEVENLABS_KEY, "")
        self._voiced = True  # until ElevenLabs refuses for good

    async def __aenter__(self) -> "Mouth":
        self._http = httpx2.AsyncClient(timeout=SPEECH_TIMEOUT)
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self._http.aclose()

    async def speak(self, text: str) -> None:
        """Speak one line. Cancelling the caller cuts it off at once."""
        if self._voiced:
            try:
                await self._stream(text)
                return
            except httpx2.HTTPStatusError as error:
                print(f"ElevenLabs refused the line ({error.response.status_code}); using macOS speech.",
                      file=sys.stderr)
                if error.response.status_code in REJECTED:
                    self._voiced = False
            except httpx2.TransportError as error:
                print(f"ElevenLabs could not be reached ({error!r}); using macOS speech for this line.",
                      file=sys.stderr)
        await say(text, SAY_VOICE)

    async def prepare(self, lines: Iterable[str]) -> None:
        """Fetch lines into the cache without playing them, so the first time they are needed they play at once."""
        for line in lines:
            if not clip_path(line).exists():
                try:
                    async with contextlib.aclosing(self._audio(line)) as audio:
                        async for _ in audio:
                            pass
                except httpx2.HTTPError as error:  # they are fetched again when first spoken
                    print(f"Could not prepare the voice ({error!r}).", file=sys.stderr)
                    return

    async def _stream(self, text: str) -> None:
        """Play the voice as it streams in; the first audio arrives a few hundred milliseconds after the request."""
        with span("speech", characters=len(text)) as fields:
            await self._play(text, fields)

    async def _play(self, text: str, fields: dict) -> None:
        start = time.perf_counter()
        speakers = None
        try:
            async with contextlib.aclosing(self._audio(text)) as audio:
                async for samples in audio:
                    if speakers is None:  # opened only once there is something to play, so a refused line opens nothing
                        fields["first_audio"] = round(time.perf_counter() - start, 3)
                        speakers = sounddevice.RawOutputStream(samplerate=OUTPUT_RATE, channels=1, dtype="int16")
                        speakers.start()
                    await asyncio.to_thread(speakers.write, samples)
            if speakers:
                await asyncio.to_thread(speakers.write, QUIET_TAIL)
                await asyncio.to_thread(speakers.stop)  # returns once the last of it has been played
        finally:
            if speakers:
                speakers.abort()  # cut off at once when cancelled; a no-op after stop
                speakers.close()

    async def _audio(self, text: str) -> AsyncIterator[bytes]:
        """A line's audio in whole samples: from the cache, or streamed from ElevenLabs and cached when short."""
        clip = clip_path(text)
        if clip.exists():
            clip.touch()  # recently played, so kept longest
            yield clip.read_bytes()
            return
        heard = bytearray()
        async with elevenlabs_request(self._http, self._key, text) as response:
            response.raise_for_status()
            partial = b""
            async for chunk in response.aiter_bytes():
                audio = partial + chunk
                whole = len(audio) - len(audio) % SAMPLE_BYTES  # a chunk can end in the middle of a sample
                partial = audio[whole:]
                heard += audio[:whole]
                yield audio[:whole]
        if len(text) <= CACHED_CHARACTERS and heard:
            keep_clip(clip, bytes(heard))
