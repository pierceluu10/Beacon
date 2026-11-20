"""What the words mean (pure), then the real Ears (Moonshine VAD and speech-to-text) fed synthesized speech through a
fake input device: no microphone, no human, no sound."""
import asyncio
import re
import time
from types import SimpleNamespace

import pytest

import fake_microphone
import main
import voice
from voice import Ears, MicrophoneSilentError, check_microphone, is_dismissal, is_stop, parse_yes_no, wake_command

CONFIG = main.Config()
NAMES = (CONFIG.agent_name,)
SILENT_SAY = "/usr/bin/true"
SHORT_ECHO_TAIL_SECONDS = 0.05
HEAR_SECONDS = 10  # ample for any clip; a test that hears nothing fails instead of hanging
SILENCE_SECONDS = 1.0
SCHEDULING_SLACK_SECONDS = 0.3
LINE_END_SECONDS = 2  # Moonshine closes a line 0.5 to 0.9 s after the speech ends
HALF_SENTENCE_SECONDS = 1.6  # cuts the wakecmd clip off after "Hey Jarvis, go to"
JUST_AFTER_SECONDS = 0.1
DEADLINE_INSIDE_SPEECH_SECONDS = 1.5  # the wakecmd clip is still being spoken at this point
REPLY_DELAY_SECONDS = 0.3  # a quick human answer, after the shortened echo tail
SHORT_CONFIRM_SECONDS = 2
SHORT_MICROPHONE_CHECK_ATTEMPTS = 2
QUESTION = "Ready to press Submit?"
RETRY = main.CONFIRM_RETRY
UNCLEAR = "thanks"  # the clip of an answer that is neither yes nor no
RESULT = "Done."
ACKNOWLEDGED_AND_DONE = [main.ACKNOWLEDGEMENT, RESULT]
GREETING = main.GREETING.format(user_name=CONFIG.user_name)
REPLY_HANDLED_SECONDS = 5  # a short reply is spoken, transcribed, joined and acted on well within this
SPEECH_SLOWER_THAN_NOMINAL = 1.2  # measured: 71 words took 24.9 s at 190 words a minute, 1.11 times the nominal 22.4 s


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
    ("", None),
    ("?!", None),
])
def test_wake_command_is_what_follows_the_name_in_the_first_three_words(transcript, command):
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
    ("Yes yes yes yes yes yes", None),
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
def test_parse_yes_no_needs_a_short_answer_of_yes_words_only_and_lets_no_win(transcript, verdict):
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
        lines.put_nowait(heard)
        work = asyncio.create_task(asyncio.sleep(HEAR_SECONDS))
        watcher = asyncio.create_task(main.stop_when_told(SimpleNamespace(hear=lines.get), work, NAMES))
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


def test_the_longest_answer_the_prompt_allows_is_spoken_before_say_is_killed():
    bound = re.search(r"under (\d+) words", main.SYSTEM_PROMPT)
    assert bound, "prompt.txt does not bound the length of the spoken answer"
    seconds = int(bound[1]) * 60 / int(voice.SAY_WORDS_PER_MINUTE) * SPEECH_SLOWER_THAN_NOMINAL
    assert seconds < voice.SAY_TIMEOUT_SECONDS, f"{bound[1]} words take {seconds:.0f} s; say is killed after {voice.SAY_TIMEOUT_SECONDS}"


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
        patch.setattr(voice, "SAY", SILENT_SAY)
        patch.setattr(voice, "ECHO_TAIL_SECONDS", SHORT_ECHO_TAIL_SECONDS)
        listening = Ears(CONFIG.stt_model, list(NAMES), CONFIG.say_voice)
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
        speak = voice.speak

        async def speak_and_be_answered(text: str, say_voice: str) -> None:
            spoken.append(text)
            await speak(text, say_voice)
            if text in replies:
                asyncio.get_running_loop().call_later(REPLY_DELAY_SECONDS, user_says, replies[text])

        monkeypatch.setattr(voice, "speak", speak_and_be_answered)
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
    verdict = run(main.confirm(ears, QUESTION))
    assert verdict is approved, f"confirm returned {verdict!r} after saying {spoken}"
    assert spoken == expected_spoken, f"said {spoken}"


@pytest.mark.parametrize("clip", ["stop", "nostop"])
def test_stop_as_the_answer_cancels_the_whole_task(ears, run, user_replies, clip):
    spoken = user_replies({QUESTION: clip})
    with pytest.raises(asyncio.CancelledError):
        verdict = run(main.confirm(ears, QUESTION))
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

    async def media_playing(self) -> bool:
        return False


@pytest.mark.parametrize("clip, expected_spoken", [
    pytest.param("wake", [*ACKNOWLEDGED_AND_DONE, GREETING], id="the name alone is greeted"),
    pytest.param("wakestop", ACKNOWLEDGED_AND_DONE, id="the name and stop end the exchange"),
])
def test_the_name_in_the_follow_up_window_is_a_wake_not_a_command(ears, run, user_says, user_replies, clip, expected_spoken):
    agent, spoken = StubAgent(), user_replies({RESULT: clip})

    async def scenario() -> None:
        conversation = asyncio.create_task(main.converse(ears, agent, CONFIG))
        try:
            user_says("wakecmd")
            while RESULT not in spoken:
                await asyncio.sleep(JUST_AFTER_SECONDS)
            await asyncio.sleep(REPLY_HANDLED_SECONDS)
        finally:
            conversation.cancel()

    run(asyncio.wait_for(scenario(), HEAR_SECONDS + REPLY_HANDLED_SECONDS))
    assert len(agent.commands) == 1 and spoken == expected_spoken, f"the agent ran {agent.commands} and said {spoken}"
