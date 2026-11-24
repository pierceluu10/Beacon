"""What the words mean (pure), then the real Ears (Moonshine VAD and speech-to-text) fed synthesized speech through a
fake input device: no microphone, no human, no sound."""
import asyncio
import re
import time
from types import SimpleNamespace

import pytest

import conftest
import fake_microphone
import main
import mouth
import side
import voice
from voice import Ears, Heard, MicrophoneSilentError, check_microphone, is_dismissal, is_stop, parse_yes_no, wake_command

CONFIG = main.Config(agent_name="Jarvis")  # the clips and the near-miss names are built around this name
NAMES = (CONFIG.agent_name,)
SHORT_ECHO_TAIL_SECONDS = 0.05
HEAR_SECONDS = 10  # ample for any clip; a test that hears nothing fails instead of hanging
SILENCE_SECONDS = 1.0
SCHEDULING_SLACK_SECONDS = 0.3
LINE_END_SECONDS = 2  # Moonshine closes a line 0.5 to 0.9 s after the speech ends
HALF_SENTENCE_SECONDS = 1.6  # cuts the wakecmd clip off after "Hey Jarvis, go to"
LONG_PAUSE_MILLISECONDS = 1800  # well past the second that cut a command off before, inside the unfinished-line wait
JUST_AFTER_SECONDS = 0.1
DEADLINE_INSIDE_SPEECH_SECONDS = 1.5  # the wakecmd clip is still being spoken at this point
REPLY_DELAY_SECONDS = 0.3  # a quick human answer, after the shortened echo tail
SHORT_CONFIRM_SECONDS = 2
SHORT_MICROPHONE_CHECK_ATTEMPTS = 2
QUESTION = "Ready to press Submit?"
RETRY = main.CONFIRM_RETRY
UNCLEAR = "thanks"  # the clip of an answer that is neither yes nor no
RESULT = "Done."
LONG_LINE_SECONDS = 5  # a spoken line long enough to be cut off in the middle
CUT_OFF_SECONDS = 0.5
REPLY_HANDLED_SECONDS = 5  # a short reply is spoken, transcribed, joined and acted on well within this
SPEECH_SLOWER_THAN_NOMINAL = 1.2  # measured: 71 words took 24.9 s at 190 words a minute, 1.11 times the nominal 22.4 s


class SilentMouth:
    """Stands in for mouth.Mouth: says nothing, at once (or after `seconds`), and tells on_line what it was asked to say."""

    def __init__(self) -> None:
        self.seconds = 0.0
        self.on_line = lambda text: None

    async def speak(self, text: str) -> None:
        self.on_line(text)
        await asyncio.sleep(self.seconds)


MOUTH = SilentMouth()


@pytest.mark.parametrize("transcript, command", [
    ("Hey Jarvis.", ""),
    ("Jarvis", ""),
    ("Hey Jarvis, go to YouTube.", "go to YouTube"),
    ("hey JARVIS open my email", "open my email"),
    ("A Jarvis, open YouTube", "open YouTube"),
    ("Hey Jervis, open YouTube", "open YouTube"),
    ("Page arvis open YouTube", "open YouTube"),
    ("Okay, hey Jarvis, stop!", "stop"),
    ("Hey, Jarvis... 2 tabs, please?", "2 tabs, please"),
    ("Hey Travis, how was your weekend?", None),
    ("Hey Davis, open YouTube", None),
    ("Well hello there Jarvis, open YouTube", None),
    ("Open YouTube and search for Jarvis", None),
    ("No, I'm not. I can genuinely play anything. Hey, Jarvis.", ""),
    ("Sorry about that. Hey Jarvis, open YouTube.", "open YouTube"),
    ("It's 5.07 now? Jarvis what day is it?", "what day is it"),
    ("Nice day. The car outside is red, Jarvis.", None),
    ("", None),
    ("?!", None),
])
def test_wake_command_is_what_follows_the_name_in_the_first_three_words_of_a_sentence(transcript, command):
    assert wake_command(transcript, NAMES) == command, f"{transcript!r} gave {wake_command(transcript, NAMES)!r}"


def test_an_alias_wakes_like_the_name():
    command = wake_command("Hey Travis, play music", (*NAMES, "Travis"))
    assert command == "play music", f"gave {command!r}"


@pytest.mark.parametrize("transcript, verdict", [
    ("Yes.", True),
    ("YES!", True),
    ("Yeah, sure.", True),
    ("Uh, yes please.", True),
    ("Go ahead.", True),
    ("Do it.", True),
    ("Yes. 2", True),
    ("Yes, go ahead and submit", True),
    ("Okay, go ahead.", True),
    ("Sure, do it please", True),
    ("Yes, go ahead and submit it", None),
    ("I'm not sure", None),
    ("Not sure.", None),
    ("That's not correct", None),
    ("It isn't correct", None),
    ("Are you sure?", None),
    ("Never submit that.", None),
    ("Not yet, do it later.", None),
    ("Correct it", None),
    ("Yes, but change the email first", None),
    ("yeah that's what she said about it yesterday", None),
    ("It was due yesterday", None),
    ("Okay.", None),
    ("ok", None),
    ("Thanks.", None),
    ("I know", None),
    ("Now.", None),
    ("42", None),
    ("?!", None),
    ("", None),
    ("No.", False),
    ("Nope!", False),
    ("No, yes.", False),
    ("Yes... no, wait.", False),
    ("Yes, cancel", False),
    ("Don't.", False),
    ("Don't do it", False),
    ("Do not submit it", False),
    ("Hold on", False),
    ("Never mind.", False),
    ("No. 5", False),
])
def test_parse_yes_no_needs_an_answer_of_yes_words_only_and_lets_no_win(transcript, verdict):
    assert parse_yes_no(transcript) is verdict, f"{transcript!r} gave {parse_yes_no(transcript)!r}"


@pytest.mark.parametrize("transcript, stops", [
    ("Stop.", True),
    ("STOP!", True),
    ("No, stop.", True),
    ("Please stop.", True),
    ("Okay, stop it now.", True),
    ("Stop everything", True),
    ("Stop 2.", True),
    ("Cancel.", True),
    ("Cancel that, please.", True),
    ("Abort!", True),
    ("Never mind.", True),
    ("Nevermind", True),
    ("Stop, stop!", True),
    ("Stop stop stop", True),
    ("Wait, stop!", True),
    ("Hey, stop!", True),
    ("Stop it, stop it now", True),
    ("Stop right now", True),
    ("Stop, Jarvis.", False),
    ("Wait.", False),
    ("Hey, stop the video", False),
    ("Cancel my order on Amazon", False),
    ("Stop the video", False),
    ("Don't stop", False),
    ("Stopping by later", False),
    ("Nonstop", False),
    ("No.", False),
    ("Thanks.", False),
    ("Yes", False),
    ("?!", False),
    ("", False),
])
def test_is_stop_only_when_the_whole_utterance_says_stop(transcript, stops):
    assert is_stop(transcript) is stops, f"{transcript!r} gave {is_stop(transcript)!r}"


@pytest.mark.parametrize("transcript, dismisses", [
    ("Thanks.", True),
    ("Thank you!", True),
    ("No thanks.", True),
    ("No, thank you", True),
    ("No.", True),
    ("Nope", True),
    ("That's all.", True),
    ("That is all", True),
    ("Nothing.", True),
    ("Goodbye", True),
    ("Good bye.", True),
    ("Stop.", True),
    ("Never mind", True),
    ("Cancel that", True),
    ("Thanks a lot!", True),
    ("Thank you so much.", True),
    ("Okay, thanks.", True),
    ("Great, thank you very much", True),
    ("No, that's all", True),
    ("That's it.", True),
    ("Nothing else.", True),
    ("Bye.", True),
    ("Thank you Jarvis", True),
    ("Never mind, Jervis.", True),
    ("Hey Jarvis, stop!", True),
    ("Hey Jarvis, thanks.", True),
    ("Hey Jarvis, that's all.", True),
    ("Jarvis", False),
    ("Hey Jarvis.", False),
    ("Hey Jarvis, stop the video", False),
    ("Thanks, next", False),
    ("That's it, open that one", False),
    ("Okay, stop the video", False),
    ("No, open the second one", False),
    ("Thanks, now open my email", False),
    ("Nothing Else Matters by Metallica", False),
    ("Cancel my order on Amazon", False),
    ("Yes", False),
    ("Okay", False),
    ("?!", False),
    ("", False),
])
def test_is_dismissal_only_when_the_whole_utterance_declines(transcript, dismisses):
    assert is_dismissal(transcript, NAMES) is dismisses, f"{transcript!r} gave {is_dismissal(transcript, NAMES)!r}"


@pytest.mark.parametrize("transcript, unfinished", [
    ("Hey Jarvis, go to", True),
    ("Hey Jarvis, go to.", True),  # the recogniser's period after a pause
    ("Open my email and", True),
    ("Search for the", True),
    ("Book a table for two,", True),
    ("Play something by...", True),
    ("Hey Jarvis, go to YouTube.", False),
    ("Hey Jarvis.", False),
    ("Yes", False),
    ("Stop.", False),
    ("What time is it?", False),
    ("", False),
])
def test_is_unfinished_only_when_the_line_stops_mid_clause(transcript, unfinished):
    assert voice.is_unfinished(transcript) is unfinished, f"{transcript!r} gave {voice.is_unfinished(transcript)!r}"


@pytest.mark.parametrize("heard, spoken, barges", [
    ("Hey Jarvis, stop.", "Here are the top results for lo-fi beats on YouTube.", True),
    ("Stop.", "The page is loading now.", True),
    ("Jarvis, open my email instead.", "I found three flights to Boston.", True),
    ("Hey Jarvis, go to YouTube.", "Hey Jarvis, go to YouTube and search for lo-fi beats.", False),
    ("Stop.", "Okay, I'll stop the video. Stop.", False),
    ("I'm Jarvis, what's up?", "Hi, I'm Jarvis, what's up?", False),
    ("Thanks.", "You're welcome.", False),
    ("Open my email", "Done.", False),
    ("", "Done.", False),
])
def test_only_a_wake_or_stop_that_is_not_the_spoken_line_barges_in(heard, spoken, barges):
    verdict = voice.barges_in(heard, spoken, NAMES)
    assert verdict is barges, f"{heard!r} while saying {spoken!r} gave {verdict!r}"


@pytest.mark.parametrize("heard, stops", [
    ("Stop.", True),
    ("Hey Jarvis, stop!", True),
    ("Jarvis, cancel that.", True),
    ("Stop: Jarvis", True),  # how Moonshine wrote the spoken "Stop, Jarvis."
    ("Never mind, Jarvis.", True),
    ("Stop stop stop stop", True),
    ("A Jarvis, stop", True),
    ("Hey Jarvis, stop the video", False),
    ("Hey Jarvis, also open Gmail.", False),
    ("Cancel my order on Amazon", False),
    ("No.", False),
])
def test_stop_watcher_cancels_the_task_only_on_a_stop(heard, stops):
    async def scenario() -> bool:
        lines = asyncio.Queue()
        lines.put_nowait(Heard(heard))
        work = asyncio.create_task(asyncio.sleep(HEAR_SECONDS))

        async def say(text: str) -> None:
            pass

        async def reply(*situation: object) -> side.Reply:
            return side.Reply("Sure.", side.CONTINUE)

        agent = SimpleNamespace(task="", steps=[], view=lambda: asyncio.sleep(0, []), tell=lambda words: None)
        watcher = asyncio.create_task(main.listen_while_working(
            SimpleNamespace(hear=lines.get, say=say), agent, SimpleNamespace(reply=reply), lambda line: work.cancel(), NAMES, []))
        await asyncio.wait({work}, timeout=SCHEDULING_SLACK_SECONDS)
        watcher.cancel()
        return work.cancelled()

    assert asyncio.run(scenario()) is stops, f"{heard!r} {'did not stop' if stops else 'stopped'} the task"


def test_a_stop_right_after_a_command_takes_it_back_instead_of_joining_it():
    lines = iter(["Stop.", None])

    async def hear(timeout: float) -> str | None:
        return next(lines)

    whole = asyncio.run(main.hear_whole(SimpleNamespace(hear=hear), "delete all of my emails"))
    assert is_dismissal(whole), f"hear_whole returned {whole!r}, which would be run as a command"


@pytest.mark.parametrize("answer", ["Stop.", "Stop, Jarvis.", "Jarvis, cancel that.", "Hey Jarvis, stop!"])
def test_a_stop_as_the_answer_to_a_question_ends_the_task_with_or_without_the_name(answer):
    async def say(text: str) -> None:
        pass

    async def hear(timeout: float) -> str:
        return answer

    with pytest.raises(asyncio.CancelledError):
        returned = asyncio.run(main.ask(SimpleNamespace(say=say, hear=hear), QUESTION, NAMES))
        print(f"{answer!r} was handed to the model as the answer {returned!r}")


def test_the_longest_answer_the_settings_allow_is_spoken_before_say_is_killed():
    bounds = [int(words) for words in re.findall(r"under (\d+) words", main.SETTINGS)]
    assert bounds, "the settings sent with every prompt do not bound the length of the spoken answer"
    seconds = max(bounds) * 60 / int(mouth.SAY_WORDS_PER_MINUTE) * SPEECH_SLOWER_THAN_NOMINAL
    assert seconds < mouth.SAY_TIMEOUT_SECONDS, f"{max(bounds)} words take {seconds:.0f} s; say is killed after {mouth.SAY_TIMEOUT_SECONDS}"


@pytest.fixture
def silent_microphone():
    fake_microphone.silent[0] = True
    yield
    fake_microphone.silent[0] = False


def test_a_microphone_that_delivers_pure_silence_raises(monkeypatch, silent_microphone):
    monkeypatch.setattr(voice, "MICROPHONE_CHECK_ATTEMPTS", SHORT_MICROPHONE_CHECK_ATTEMPTS)
    with pytest.raises(MicrophoneSilentError):
        check_microphone()


def test_a_live_microphone_passes_the_check():
    check_microphone()


@pytest.fixture(scope="module")
def run():
    """Runs a coroutine on the one event loop the module's Ears live on."""
    loop = asyncio.new_event_loop()
    yield loop.run_until_complete
    loop.close()


@pytest.fixture(scope="module")
def ears(run):
    """The real Ears on the fake microphone, loaded once; speaking is silent and nearly instant."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(voice, "ECHO_TAIL_SECONDS", SHORT_ECHO_TAIL_SECONDS)
        listening = Ears(main.STT_MODEL, list(NAMES), MOUTH, wake=True)
        run(listening.__aenter__())
        yield listening
        run(listening.__aexit__(None, None, None))


@pytest.fixture
def user_says(clips):
    """Speaks a clip (or only its first seconds) into the fake microphone and returns how long it lasts."""
    def speak_clip(name: str, seconds: float | None = None) -> float:
        audio = fake_microphone.clip(clips / f"{name}.wav", seconds)
        fake_microphone.pending.append(audio)
        return len(audio) / fake_microphone.RATE

    yield speak_clip
    fake_microphone.pending.clear()


@pytest.fixture
def heard(ears, run, user_says):
    """What the Ears make of a clip."""
    def hear_clip(name: str) -> str:
        user_says(name)
        text = run(ears.hear(HEAR_SECONDS))
        assert text, f"nothing heard for the clip {name!r} within {HEAR_SECONDS} s"
        return text

    return hear_clip


@pytest.fixture
def user_replies(monkeypatch, user_says):
    """Call with {line the agent speaks: clip the user answers it with}; returns the lines spoken, as they happen."""
    def answer_with(replies: dict[str, str]) -> list[str]:
        spoken: list[str] = []

        def answer(text: str) -> None:
            spoken.append(text)
            if text in replies:  # the answer starts once the line, spoken in no time, and the echo tail are over
                asyncio.get_running_loop().call_later(REPLY_DELAY_SECONDS, user_says, replies[text])

        monkeypatch.setattr(MOUTH, "on_line", answer)
        return spoken

    return answer_with


def test_bare_name_is_heard_as_a_wake_with_no_command(heard):
    text = heard("wake")
    assert wake_command(text, NAMES) == "", f"heard {text!r}"


def test_wake_in_one_breath_carries_the_command(heard):
    text = heard("wakecmd")
    command = wake_command(text, NAMES)
    assert command and "youtube" in command.lower(), f"heard {text!r}, command {command!r}"


def test_hey_travis_is_heard_but_does_not_wake(heard):
    text = heard("travis")
    assert wake_command(text, NAMES) is None, f"heard {text!r}"


def test_spoken_yes_is_a_yes(heard):
    text = heard("yes")
    assert parse_yes_no(text) is True, f"heard {text!r}"


def test_spoken_no_is_a_no(heard):
    text = heard("no")
    assert parse_yes_no(text) is False, f"heard {text!r}"


def test_spoken_no_stop_is_both_a_stop_and_a_no(heard):
    text = heard("nostop")
    assert is_stop(text) and parse_yes_no(text) is False, f"heard {text!r}"


def test_spoken_stop_is_a_stop(heard):
    text = heard("stop")
    assert is_stop(text), f"heard {text!r}"


def test_spoken_thanks_dismisses(heard):
    text = heard("thanks")
    assert is_dismissal(text), f"heard {text!r}"


def test_a_pause_mid_command_splits_the_transcript_and_hear_whole_joins_it(ears, run, user_says):
    async def scenario() -> tuple[str | None, str | None]:
        user_says("pause_700")
        first = await ears.hear(HEAR_SECONDS)
        return first, await main.hear_whole(ears, wake_command(first, NAMES))

    first, whole = run(scenario())
    assert "search" not in first.lower(), f"the pause did not split the clip, so nothing was joined: {first!r}"
    assert "youtube" in whole.lower() and "search" in whole.lower(), f"{first!r} was joined into {whole!r}"


def test_a_long_pause_after_an_unfinished_clause_is_waited_out(ears, run, tmp_path):
    path = tmp_path / "pause_after_for.wav"
    conftest._synthesize(f"Hey Jarvis, search for [[slnc {LONG_PAUSE_MILLISECONDS}]] lo-fi beats.", path)
    fake_microphone.pending.append(fake_microphone.clip(path))

    async def scenario() -> str | None:
        return await main.hear_whole(ears, await ears.hear(HEAR_SECONDS))

    try:
        text = run(scenario())
    finally:
        fake_microphone.pending.clear()
    assert text and re.search(r"\bfor\W+\w", text, re.IGNORECASE), f"the pause cut the command to {text!r}"


def test_no_continuation_costs_only_the_grace(ears, run):
    started = time.perf_counter()
    whole = run(main.hear_whole(ears, "open the first one"))
    elapsed = time.perf_counter() - started
    assert whole == "open the first one", f"returned {whole!r}"
    grace = main.CONTINUATION_SECONDS
    assert grace <= elapsed < grace + SCHEDULING_SLACK_SECONDS, f"took {elapsed:.2f} s"


def test_silence_times_out_with_nothing(ears, run):
    started = time.perf_counter()
    text = run(ears.hear(SILENCE_SECONDS))
    elapsed = time.perf_counter() - started
    assert text is None, f"heard {text!r} in silence"
    assert SILENCE_SECONDS <= elapsed < SILENCE_SECONDS + SCHEDULING_SLACK_SECONDS, f"took {elapsed:.2f} s"


def test_speech_begun_before_the_deadline_is_returned_whole(ears, run, user_says):
    started = time.perf_counter()
    user_says("wakecmd")
    text = run(ears.hear(DEADLINE_INSIDE_SPEECH_SECONDS))
    elapsed = time.perf_counter() - started
    assert text and "search" in text.lower(), f"heard {text!r} after {elapsed:.2f} s"


def test_speech_nobody_was_listening_for_is_not_delivered_later(ears, run, user_says):
    async def scenario() -> str | None:
        await asyncio.sleep(user_says("yes") + LINE_END_SECONDS)
        return await ears.hear(SILENCE_SECONDS)

    stale = run(scenario())
    assert stale is None, f"a listener that arrived after the speech still got {stale!r}"


@pytest.fixture
def wake_off(ears, run):
    """The wake word switched off: the microphone opens only for the key or a question."""
    ears.set_wake(False)
    run(asyncio.sleep(voice.MICROPHONE_LINGER_SECONDS + JUST_AFTER_SECONDS))
    yield
    ears.set_wake(True)


def device_open(ears: Ears) -> bool:
    return ears._mic._sd_stream.running


def test_with_the_wake_word_off_the_microphone_is_closed_and_nothing_is_heard(ears, run, user_says, wake_off):
    async def scenario() -> Heard | None:
        assert not device_open(ears), "the audio device is still open with the wake word off"
        await ears.say("Ready.")  # speaking must not switch the microphone back on
        assert not device_open(ears), "speaking reopened the audio device"
        watcher = asyncio.create_task(ears.hear())
        await asyncio.sleep(user_says("wakecmd") + LINE_END_SECONDS)
        watcher.cancel()
        return None if watcher.cancelled() or not watcher.done() else watcher.result()

    assert run(scenario()) is None


def test_with_the_wake_word_off_a_question_still_opens_the_microphone_for_its_answer(ears, run, user_says, wake_off):
    async def scenario() -> Heard | None:
        question = asyncio.create_task(ears.hear(HEAR_SECONDS))
        await asyncio.sleep(JUST_AFTER_SECONDS)
        assert device_open(ears) and ears.activity == voice.HEARING, "the microphone stayed closed for the answer"
        user_says("yes")
        return await question

    answer = run(scenario())
    assert parse_yes_no(answer) is True, f"heard {answer!r}"
    run(asyncio.sleep(voice.MICROPHONE_LINGER_SECONDS + JUST_AFTER_SECONDS))
    assert not device_open(ears), "the microphone stayed open after the answer"


def test_holding_the_key_hears_one_whole_utterance_even_across_a_pause(ears, run, user_says, wake_off):
    async def scenario() -> Heard | None:
        watcher = asyncio.create_task(ears.hear())
        ears.push(True)
        assert device_open(ears) and ears.activity == voice.HEARING, "the key did not open the microphone"
        await asyncio.sleep(user_says("pause_700") + LINE_END_SECONDS)
        assert not watcher.done(), f"delivered {watcher.result()!r} before the key was let go"
        ears.push(False)
        return await asyncio.wait_for(watcher, HEAR_SECONDS)

    heard = run(scenario())
    assert heard.pushed and "youtube" in heard.lower() and "search" in heard.lower(), f"heard {heard!r}"
    assert main.addressed(heard, NAMES) and "search" in main.addressed(heard, NAMES).lower()
    assert not device_open(ears), "the microphone stayed open after the key was let go"


def test_the_key_cuts_off_what_beacon_is_saying(ears, run, monkeypatch):
    monkeypatch.setattr(MOUTH, "seconds", LONG_LINE_SECONDS)

    async def scenario() -> float:
        started = time.perf_counter()
        speaking = asyncio.create_task(ears.say("A long answer."))
        await asyncio.sleep(CUT_OFF_SECONDS)
        assert ears.activity == voice.SPEAKING, ears.activity
        ears.push(True)
        await speaking
        ears.push(False)
        return time.perf_counter() - started

    took = run(scenario())
    assert took < CUT_OFF_SECONDS + SCHEDULING_SLACK_SECONDS, f"say ran for {took:.2f} s after the key was pressed"


def test_the_name_and_stop_while_beacon_speaks_cut_it_off_and_reach_the_next_listener(ears, run, user_says, monkeypatch):
    monkeypatch.setattr(MOUTH, "seconds", LONG_LINE_SECONDS * 2)

    async def scenario() -> tuple[float, Heard | None]:
        started = time.perf_counter()
        speaking = asyncio.create_task(ears.say("Here are the top results for lo-fi beats, the most watched one first."))
        await asyncio.sleep(CUT_OFF_SECONDS)
        user_says("wakestop")
        await speaking
        took = time.perf_counter() - started
        return took, await ears.hear(SILENCE_SECONDS)

    took, heard_next = run(scenario())
    assert took < LONG_LINE_SECONDS * 2, f"say ran its whole {took:.2f} s through the barge-in"
    assert heard_next and is_stop(heard_next, NAMES), f"the next listener got {heard_next!r}"


def test_beacon_hearing_its_own_line_does_not_cut_it_off_or_reach_a_listener(ears, run, user_says, monkeypatch):
    monkeypatch.setattr(MOUTH, "seconds", LONG_LINE_SECONDS * 2)

    async def scenario() -> tuple[float, Heard | None]:
        started = time.perf_counter()
        user_says("wakecmd")  # the speakers playing the line back into the microphone
        await ears.say(conftest.CLIPS["wakecmd"])
        took = time.perf_counter() - started
        return took, await ears.hear(SILENCE_SECONDS)

    took, heard_next = run(scenario())
    assert took >= LONG_LINE_SECONDS * 2, f"its own echo cut the line off after {took:.2f} s"
    assert heard_next is None, f"its own echo reached the next listener as {heard_next!r}"


def test_the_recogniser_is_restarted_so_it_does_not_keep_every_line_it_ever_heard(ears, heard):
    lines_kept = []
    for _ in range(voice.RESTART_AFTER_LINES + 2):
        heard("yes")
        lines_kept.append(len(ears._mic.mic_stream.update_transcription().lines))
    assert max(lines_kept) <= voice.RESTART_AFTER_LINES, f"lines kept after each utterance: {lines_kept}"


def test_newest_listener_gets_the_utterance_and_the_older_one_the_next(ears, run, user_says):
    async def scenario() -> None:
        watcher = asyncio.create_task(ears.hear())
        question = asyncio.create_task(ears.hear(HEAR_SECONDS))
        try:
            user_says("yes")
            answer = await question
            assert parse_yes_no(answer) is True, f"the question heard {answer!r}"
            assert not watcher.done(), f"the watcher also got {watcher.result()!r}"
            user_says("stop")
            next_line = await asyncio.wait_for(watcher, HEAR_SECONDS)
            assert is_stop(next_line), f"the watcher heard {next_line!r}"
        finally:
            watcher.cancel()

    run(scenario())


@pytest.mark.parametrize("quiet_wait_seconds", [
    pytest.param(voice.QUIET_WAIT_SECONDS, id="say waits for the phrase to end"),
    pytest.param(0, id="say cuts in and flushes the phrase"),
])
def test_half_a_sentence_before_a_question_goes_to_the_watcher_not_into_the_answer(
        ears, run, user_says, monkeypatch, quiet_wait_seconds):
    monkeypatch.setattr(voice, "QUIET_WAIT_SECONDS", quiet_wait_seconds)

    async def scenario() -> None:
        watcher = asyncio.create_task(ears.hear())
        try:
            await asyncio.sleep(user_says("wakecmd", HALF_SENTENCE_SECONDS) + JUST_AFTER_SECONDS)
            assert not watcher.done(), f"the phrase had already ended as {watcher.result()!r}; nothing was in progress"
            started = time.perf_counter()
            await ears.say(QUESTION)
            said_in = time.perf_counter() - started
            assert watcher.done(), f"the watcher got nothing while the question was spoken ({said_in:.2f} s)"
            user_says("yes")
            answer = await ears.hear(HEAR_SECONDS)
            assert parse_yes_no(answer) is True, f"watcher got {watcher.result()!r}, the answer was {answer!r}"
        finally:
            watcher.cancel()

    run(scenario())


@pytest.mark.parametrize("replies, approved, expected_spoken", [
    pytest.param({QUESTION: "yes"}, True, [QUESTION], id="yes"),
    pytest.param({QUESTION: "no"}, False, [QUESTION], id="no"),
    pytest.param({}, False, [QUESTION], id="silence"),
    pytest.param({QUESTION: UNCLEAR, RETRY: "yes"}, True, [QUESTION, RETRY], id="unclear then yes"),
    pytest.param({QUESTION: UNCLEAR, RETRY: UNCLEAR}, False, [QUESTION, RETRY], id="unclear twice"),
    pytest.param({QUESTION: UNCLEAR}, False, [QUESTION, RETRY], id="unclear then silence"),
])
def test_confirm_approves_only_on_a_spoken_yes(ears, run, user_replies, monkeypatch, replies, approved, expected_spoken):
    monkeypatch.setattr(main, "CONFIRM_SECONDS", SHORT_CONFIRM_SECONDS)
    spoken = user_replies(replies)
    verdict = run(main.confirm(ears, QUESTION, NAMES))
    assert verdict is approved, f"confirm returned {verdict!r} after saying {spoken}"
    assert spoken == expected_spoken, f"said {spoken}"


@pytest.mark.parametrize("clip", ["stop", "nostop", "wakestop"])
def test_stop_as_the_answer_cancels_the_whole_task(ears, run, user_replies, clip):
    spoken = user_replies({QUESTION: clip})
    with pytest.raises(asyncio.CancelledError):
        verdict = run(main.confirm(ears, QUESTION, NAMES))
        print(f"confirm returned {verdict!r} after saying {spoken}")


class StubAgent:
    """Answers every command at once with RESULT; nothing is playing, so the follow-up window opens."""

    def __init__(self) -> None:
        self.commands: list[str] = []

    async def run(self, command: str) -> str:
        self.commands.append(command)
        return RESULT

    def forget(self) -> None:
        pass

    def untold(self) -> list[str]:
        return []

    async def media_playing(self) -> bool:
        return False


@pytest.mark.parametrize("clip", ["wake", "wakestop"], ids=["the name alone", "the name and stop"])
def test_the_name_in_the_follow_up_window_is_a_wake_not_a_command(ears, run, user_says, user_replies, clip):
    agent, spoken = StubAgent(), user_replies({RESULT: clip})
    memory = SimpleNamespace(stamp=tuple)

    async def scenario() -> None:
        conversation = asyncio.create_task(main.converse(ears, agent, SimpleNamespace(), memory, CONFIG))
        try:
            user_says("wakecmd")
            while RESULT not in spoken:
                await asyncio.sleep(JUST_AFTER_SECONDS)
            await asyncio.sleep(REPLY_HANDLED_SECONDS)
        finally:
            conversation.cancel()

    run(asyncio.wait_for(scenario(), HEAR_SECONDS + REPLY_HANDLED_SECONDS))
    assert len(agent.commands) == 1 and spoken == [RESULT], f"the agent ran {agent.commands} and said {spoken}"
