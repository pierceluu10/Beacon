# Hey

Say "Hey Jarvis", speak a command, and watch a real Chrome window carry it out.

```
You:    "Hey Jarvis."
Jarvis: "Hi Pierce."
You:    "Go to YouTube and search for lo-fi hip hop radio."
Jarvis: "On it."            (Chrome navigates, types and clicks while you watch)
Jarvis: "The results are up. The first one is a live stream."
```

Speech recognition and speech output run on this Mac. The only paid service is the Anthropic API.

## Run it

```bash
export ANTHROPIC_API_KEY=...       # if it is not already in your shell profile
uv sync
uv run python main.py
```

The first run asks for microphone access for your terminal and opens a Chrome window with its own profile
(`~/.hey/chrome-profile`). Log in to the sites you want it to use in that window once; the logins persist.
When you hear "Ready", say "Hey Jarvis".

- Say the name and the command in one breath, or say the name, wait for "Hi Pierce", then speak.
- After it answers you have 8 seconds to give a follow-up without the name.
- Say "stop" while it is working to cancel. "Thanks" or silence ends the exchange.
- It asks out loud before anything that submits, sends, pays, books, posts or deletes. Only a short spoken
  "yes" goes ahead; silence means no.
- It never types passwords, card numbers or verification codes, and never solves CAPTCHAs. It asks you to do
  those in the window and say "continue".

## Job applications

1. Put your resume at `~/.hey/uploads/resume.pdf`. Every file in that folder can be attached by the agent, so
   keep only files you are happy to send.
2. Write `~/.hey/facts.md` in plain text: name, email, phone, links, location, work authorization, notice
   period, salary expectations. It answers form questions only from this file and what you say; anything
   missing, it asks you. No passwords.
3. Open the job posting (or tell it the address) and say "Hey Jarvis, apply to this job with my resume".

## Configuration

Optional, in `~/.hey/config.toml`. Unknown keys are an error.

| Key | Default | Meaning |
|---|---|---|
| `agent_name` | `"Jarvis"` | The wake name, one word. Any name works; no training. |
| `wake_aliases` | `[]` | Other spellings the recogniser produces for your name. |
| `user_name` | `"Pierce"` | Used in the greeting. |
| `model` | `"claude-opus-5-5"` | Any Anthropic model id. |
| `profile_dir` | `"~/.hey/chrome-profile"` | The agent's own Chrome profile. |
| `resume_path` | `"~/.hey/uploads/resume.pdf"` | "My resume". Its folder is the only place uploads can come from. |
| `facts_path` | `"~/.hey/facts.md"` | Facts about you for forms. |
| `blocked_hosts` | `[]` | Sites it may never open, for example your bank. |
| `stt_model` | `"MEDIUM_STREAMING"` | Or `"SMALL_STREAMING"` (less memory, slightly less accurate). |
| `say_voice` | `"Samantha"` | Any voice from `say -v '?'`. |

## How it works

| File | Job |
|---|---|
| `main.py` | Configuration and the conversation loop. |
| `voice.py` | Moonshine speech-to-text on the microphone, macOS `say`, and wake / yes / no / stop parsing. |
| `agent.py` | Claude driving Chrome through the Playwright MCP server. |
| `gate.py` | The rules for what needs a spoken yes and what is refused. |
| `inspect.js` | Runs in the page to describe the element a click or key press would hit, so the gate judges the real DOM, not the model's description. |
| `prompt.txt` | The system prompt. |

## Tests

```bash
uv run pytest                 # gate rules, real speech recognition on synthesized clips, real headless Chrome
RUN_LIVE=1 uv run pytest      # adds the live Claude and YouTube runs (a few cents)
```

No test needs a microphone or a person: clips are made with `say` and fed through a fake input device.

## Limits

- The gate reads labels the page wrote. A hostile page can label a pay button "Next"; you watching the window
  is the last defence. Keep banking and your main email out of this profile.
- It cannot hear you while it is speaking.
- A pause longer than about a second in the middle of a command cuts the command off.
- LinkedIn is off limits by design (its terms forbid automation).
