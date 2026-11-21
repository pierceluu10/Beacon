"""Speech out: ElevenLabs streamed straight to the speakers when ELEVENLABS_API_KEY is set, otherwise macOS `say`."""
import asyncio
import contextlib
import os
import sys

import httpx2
import sounddevice

SAY = "/usr/bin/say"
SAY_WORDS_PER_MINUTE = "190"
SAY_TIMEOUT_SECONDS = 20  # `say` hung once in testing here; the prompts hold an answer to 35 words, about 13 s
CHIME = "/System/Library/Sounds/Tink.aiff"
AFPLAY = "/usr/bin/afplay"
ELEVENLABS_KEY_VARIABLE = "ELEVENLABS_API_KEY"
ELEVENLABS_STREAM = "https://api.elevenlabs.io/v1/text-to-speech/{voice}/stream"
OUTPUT_FORMAT = "pcm_24000"  # raw 16-bit mono: playable as it arrives, and available on every plan
OUTPUT_RATE = 24_000
SAMPLE_BYTES = 2
# Stopping the stream right after the last sample makes the speakers crackle; measured on a MacBook Pro, 0.25 s of
# silence first still crackled sometimes, 0.5 s never did.
QUIET_TAIL = bytes(OUTPUT_RATE // 2 * SAMPLE_BYTES)
ELEVENLABS_TIMEOUT = httpx2.Timeout(10, connect=5)
REJECTED = (401, 402, 403)  # bad key, no credits left, or the voice is not on this account: no point retrying each line


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

    def __init__(self, voice: str, model: str, say_voice: str) -> None:
        self._voice, self._model, self._say_voice = voice, model, say_voice
        self._key = os.environ.get(ELEVENLABS_KEY_VARIABLE, "")

    async def __aenter__(self) -> "Mouth":
        self._http = httpx2.AsyncClient(timeout=ELEVENLABS_TIMEOUT)
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self._http.aclose()

    async def speak(self, text: str) -> None:
        """Speak one line. Cancelling the caller cuts it off at once."""
        if self._key:
            try:
                await self._stream(text)
                return
            except httpx2.HTTPStatusError as error:
                print(f"ElevenLabs refused the line ({error.response.status_code}); using macOS speech.", file=sys.stderr)
                if error.response.status_code in REJECTED:
                    self._key = ""
            except httpx2.TransportError as error:
                print(f"ElevenLabs could not be reached ({error!r}); using macOS speech for this line.", file=sys.stderr)
        await say(text, self._say_voice)

    async def _stream(self, text: str) -> None:
        """Play the voice as it streams in; the first audio arrives a few hundred milliseconds after the request."""
        request = self._http.stream("POST", ELEVENLABS_STREAM.format(voice=self._voice), params={"output_format": OUTPUT_FORMAT},
                                    headers={"xi-api-key": self._key}, json={"text": text, "model_id": self._model})
        async with request as response:
            response.raise_for_status()
            speakers = sounddevice.RawOutputStream(samplerate=OUTPUT_RATE, channels=1, dtype="int16")
            speakers.start()
            try:
                partial = b""
                async for chunk in response.aiter_bytes():
                    audio = partial + chunk
                    whole = len(audio) - len(audio) % SAMPLE_BYTES  # a chunk can end in the middle of a sample
                    partial = audio[whole:]
                    await asyncio.to_thread(speakers.write, audio[:whole])
                await asyncio.to_thread(speakers.write, QUIET_TAIL)
                await asyncio.to_thread(speakers.stop)  # returns once the last of it has been played
            finally:
                speakers.abort()  # cut off at once when cancelled; a no-op after stop
                speakers.close()
