"""Quick side conversations on a fast model, apart from the task: the greeting at start-up, and a spoken reply when the
user talks while a task is running ("what are you seeing?", "actually, use the other one", "stop")."""
from typing import Literal, NamedTuple

from anthropic import AsyncAnthropic

GREETING_TOKENS = 60
REPLY_TOKENS = 300
CONTINUE, CHANGE, STOP = "continue", "change", "stop"
GREETING_PROMPT = ("You are {agent_name}, a voice assistant that uses the user's computer for them. You have just started up. "
                   "Say one short, warm line of at most 8 words, like \"Hey Sam, what are we doing today?\": greet the user, "
                   "by name only if their notes give it, and ask what they want to do. Do not introduce yourself. Plain "
                   "text, nothing else.")
NO_NOTES = "The user has not written any notes."
NOTES = "The user's notes:\n{notes}"
REPLY_PROMPT = (
    "You are {agent_name}, a voice assistant in the middle of a task on the user's computer. The user just spoke to you "
    "while you work. Reply out loud in one or two short plain sentences, as a person would: answer questions from what is "
    "on screen and what you have done, and acknowledge requests. Never claim something happened that the steps do not "
    "show. Then decide what happens to the task: continue (a question or a remark: nothing changes), change (they want "
    "something different, more, or less: the task is handed their words and adapts), or stop (they want it to stop).")
REPLY_TOOL = {
    "name": "reply",
    "description": "What to say out loud now, and what happens to the task.",
    "input_schema": {"type": "object", "properties": {
        "say": {"type": "string", "description": "The spoken reply, one or two short plain sentences."},
        "then": {"type": "string", "enum": [CONTINUE, CHANGE, STOP]},
    }, "required": ["say", "then"]},
}
SITUATION = "The task: {task}\n\nWhat you have done so far:\n{steps}\n\nThe user just said: {said}"
NO_STEPS = "(nothing yet)"


class Reply(NamedTuple):
    say: str
    then: Literal["continue", "change", "stop"]


class Side:
    """The fast model, for lines that must come back in about a second."""

    def __init__(self, model: str, agent_name: str) -> None:
        self._model, self._agent_name = model, agent_name
        self._anthropic = AsyncAnthropic()

    async def __aenter__(self) -> "Side":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self._anthropic.close()

    async def greet(self, notes: str) -> str:
        reply = await self._anthropic.messages.create(
            model=self._model, max_tokens=GREETING_TOKENS, system=GREETING_PROMPT.format(agent_name=self._agent_name),
            messages=[{"role": "user", "content": NOTES.format(notes=notes) if notes.strip() else NO_NOTES}])
        return " ".join(block.text for block in reply.content if block.type == "text").strip()

    async def reply(self, said: str, task: str, steps: list[str], view: list[dict]) -> Reply:
        """What to say to words spoken mid-task, given the task, its steps and what is on screen right now."""
        situation = SITUATION.format(task=task, steps="\n".join(steps) or NO_STEPS, said=said)
        reply = await self._anthropic.messages.create(
            model=self._model, max_tokens=REPLY_TOKENS, system=REPLY_PROMPT.format(agent_name=self._agent_name),
            tools=[REPLY_TOOL], tool_choice={"type": "tool", "name": REPLY_TOOL["name"]},
            messages=[{"role": "user", "content": [*view, {"type": "text", "text": situation}]}])
        chosen = next(block.input for block in reply.content if block.type == "tool_use")
        return Reply(chosen["say"], chosen["then"] if chosen["then"] in (CONTINUE, CHANGE, STOP) else CONTINUE)
