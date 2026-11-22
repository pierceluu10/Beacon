<p align="center">
  <img src="logo.png" width="96" alt="Beacon logo: a pointer with signal arcs on a dark tile">
</p>
<h1 align="center"><code>beacon</code></h1>
<p align="center"><b>Talk to your Mac. Watch it do the work.</b></p>
<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT license"></a>
  <img src="https://img.shields.io/badge/macOS-Apple%20silicon-lightgrey" alt="macOS on Apple silicon">
  <img src="https://img.shields.io/badge/python-3.12-blue" alt="Python 3.12">
</p>

Beacon is a voice computer-use agent for macOS. Hold a key or say "Hey Beacon", say what you want, and it works
on your screen with the real mouse and keyboard, in any app. Talk to it while it works: ask what it sees, change
the plan, or tell it to stop.

```
Beacon: "Hey Sam, what are we doing today?"
You:    (holding ⌥Space) "Find a quiet café near me that's open late and add it to my calendar for Friday."
Beacon: "Looking that up in Maps."
You:    (holding ⌥Space) "What are you seeing right now?"
Beacon: "Three cafés on the map; the closest is open till eleven."
You:    (holding ⌥Space) "Actually make it Saturday."
Beacon: "Got it, Saturday."
```

Speech recognition runs on this Mac. The paid services are the Anthropic API and, if you add a key, ElevenLabs or Cartesia.

## Run it

```bash
export ANTHROPIC_API_KEY=...       # if it is not already in your shell profile
export ELEVENLABS_API_KEY=...      # optional: a natural voice instead of macOS `say` (see Voice for Cartesia)
uv sync
uv run python main.py
```

The first run asks for microphone access for your terminal, then Screen Recording and Accessibility (System
Settings, Privacy and Security). Allow them, quit and reopen the terminal, and start Beacon again. It greets you
by name if your memory says who you are.

## Talking to it

- **Push to talk**: hold ⌥Space, speak, let go. Pressing it while Beacon talks cuts it off.
- **"Hey Beacon"**: say the name and the command, or the name, wait for the chime, then speak. Works while it is
  talking too. Turn it off in the menu to keep the microphone closed until you hold the key.

After an answer you have 8 seconds to follow up without either. Mid-task you can **ask** ("what are you
seeing?"), **redirect** ("make it Saturday") or **stop** ("stop", "never mind").

## The menu bar

The mark shows a microphone while it hears you, pulsing dots while it works, a waveform while it talks, and dims
when off. The menu holds the wake-word switch, the push-to-talk key recorder, Memory, where it works (**My whole
Mac**, **My Chrome** or **Separate Chrome window**), Turn Off and Quit. Choices are saved in
`~/.beacon/settings.json`.

## Memory

**Memory…** opens notes (`~/.beacon/memory.md`: who you are, how you like things done) and files
(`~/.beacon/memory/`: PDFs, images and text are read). Both go into each new conversation. In the browser modes the
memory folder is the only place uploads come from. Never put passwords in memory.

## Voice

Set `ELEVENLABS_API_KEY` (default, voice "Vance") or `CARTESIA_API_KEY` with `voice_service = "cartesia"`.
`voice` and `voice_model` pick another voice or model. Without a key, or if the key is rejected, it uses macOS `say`.

## Where it works

- **Whole Mac** (default): screenshots plus the real mouse and keyboard, in any app, main display only. No spoken
  confirmation: it does what you ask, including sending and buying. Every step sends a screenshot to the
  Anthropic API.
- **Separate Chrome window**: its own profile at `~/.beacon/chrome-profile`; log in to sites there once. With your
  everyday Chrome in front, "Hey Beacon, what is this page about?" opens the same address in its window.
- **My Chrome** (opt-in): a tab in the Chrome you already use, with every login in reach. Install the
  [Playwright Extension](https://chromewebstore.google.com/detail/playwright-extension/mmlmfjhmonkocbjadbfplnigmagldckm),
  put its token in `~/.beacon/config.toml` as `extension_token`, and `chmod 600` that file. The token lets any program
  on this Mac attach to your Chrome, so rotate it if it leaks.

## Security

Beacon acts with your accounts, so the browser modes check every action in code before it runs, rather than
trusting the model:

- **A spoken yes before anything irreversible.** [`gate.py`](gate.py) `judge()` asks out loud before a click or
  Enter that submits, sends, pays, books, posts, deletes, finishes or creates an account, and before any step on
  a checkout or payment page whatever the button says (`_purchase()`). Only a plain "yes" goes ahead; silence is
  no.
- **It judges the real page, not the model's description.** [`inspect.js`](inspect.js) runs in the page and reads
  the element the action would hit, including names drawn with CSS, `aria-label` and `title`.
- **No secrets.** Password, card, CVV, PIN and verification-code fields are recognised by type, autocomplete,
  name and label, and typing into them is refused; you fill those in yourself. Whatever you type there is replaced
  with `(hidden)` before the page is sent to the API (`SECRET_VALUES_JS` in [`agent.py`](agent.py), using the same
  rule).
- **Only public web addresses.** `url_refusal()` in [`gate.py`](gate.py) allows plain `http(s)` only and refuses
  any site name that resolves to this machine or a private network. `blocked_hosts` adds your own no-go sites;
  `linkedin.com` is always blocked (`ALWAYS_BLOCKED_HOSTS` in [`main.py`](main.py)).
- **Files stay put.** Uploads come only from the memory folder (the browser server's working directory in
  [`agent.py`](agent.py)), saving pages to files is refused (`FILE_REFUSAL`), and the Chrome profile may not sit
  inside the memory folder.
- **Speech stays local.** Recognition runs on this Mac with Moonshine ([`voice.py`](voice.py)); API keys are read
  from the environment, never written to disk.

## Configuration

Optional, in `~/.beacon/config.toml`. Unknown keys are an error. The menu's choices live in
`~/.beacon/settings.json`, not here.

| Key | Default | Meaning |
|---|---|---|
| `agent_name` | `"Beacon"` | The wake name, one word. Any name works; no training. |
| `wake_aliases` | `[]` | Other spellings the recogniser produces for your name. |
| `model` | `"claude-opus-5-5"` | Any Anthropic model id. The tests run on the default; `claude-sonnet-5-5` was tried by hand on multi-step tasks. |
| `quick_model` | `"claude-haiku-5-5"` | The greeting and the quick replies while a task runs. |
| `profile_dir` | `"~/.beacon/chrome-profile"` | The agent's own Chrome profile. |
| `blocked_hosts` | `[]` | Sites it may never navigate to, for example your bank. Links and redirects that land there are not checked. |
| `stt_model` | `"MEDIUM_STREAMING"` | Or `"SMALL_STREAMING"` (less memory, slightly less accurate). |
| `voice_service` | `"elevenlabs"` | Or `"cartesia"`. See [Voice](#voice). |
| `voice` | `""` | A voice id from that service; empty for its default. |
| `voice_model` | `""` | A model from that service; empty for its default. |
| `say_voice` | `"Samantha"` | The macOS voice without a voice service key; any voice from `say -v '?'`. |
| `extension_token` | `""` | Set it to work in your own Chrome through the Playwright Extension (see above). |

## How it works

| File | Job |
|---|---|
| `main.py` | Configuration and the conversation loop, including listening while a task runs. |
| `voice.py` | Moonshine speech-to-text on the microphone, push-to-talk, and wake / yes / no / stop parsing. |
| `mouth.py` | Speech out: ElevenLabs or Cartesia streaming, or macOS `say` without a key. |
| `side.py` | The fast model behind the greeting and the quick replies mid-task. |
| `memory.py` | Turns your notes and memory files into the first message of a conversation. |
| `agent.py` | Claude driving Chrome through the Playwright MCP server. |
| `gate.py` | The rules for what needs a spoken yes and what is refused. |
| `inspect.js` | Runs in the page to describe the element a click or key press would hit, so the gate judges the real DOM, not the model's description. |
| `everyday.py` | Reads the address and title of the front tab of your everyday Chrome when that Chrome is the app in front. |
| `front_tab.js` | The macOS script `everyday.py` runs. It addresses the Chrome in front by process id, because every Chrome answers to the same name. |
| `menubar.py`, `menubar.swift`, `mark.png` | The menu bar item, its menu, the key recorder and the Memory window: a small native helper, compiled on first run (needs `xcode-select --install`). |
| `mac.py`, `mac_prompt.txt` | The whole-Mac mode: screenshots, mouse and keyboard through Anthropic's computer toolset. |
| `prompt.txt` | The system prompt for the browser modes. |

## Tests

```bash
uv run pytest                 # gate rules, real speech recognition on synthesized clips, real headless Chrome
RUN_LIVE=1 uv run pytest      # adds the live Claude and YouTube runs (a few cents)
```

No test needs a microphone or a person: clips are made with `say` and fed through a fake input device.

## Limits

- The whole-Mac mode has no gate: it asks nothing before acting. Stop it by voice or the key.
- In the browser modes, dropdowns, links of more than four words and page keyboard shortcuts are not questioned,
  and a hostile page can still disguise a pay step. Keep banking and main email out of its profile.
- Typed secrets are hidden only in the page itself, not inside embedded frames (most card forms). Press the page's
  own button before you say "continue".
- The address check uses this Mac's DNS lookup; a site that answers Chrome differently is not caught. Links and
  redirects into `blocked_hosts` are not checked.
- While it talks, a bare "stop" that also appears in its own line is taken as echo; say "Hey Beacon, stop".
- The push-to-talk key is taken from every other app while Beacon runs.
- A command stops after 40 steps (60 on the whole Mac).
