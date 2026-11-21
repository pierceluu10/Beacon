<p align="center">
  <img src="logo.png" width="96" alt="Beacon logo: a small bird in a white rounded square">
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

Two ways in, and both can be on at once:

- **Push to talk**: hold the key (⌥Space by default), speak, let go. Everything said while the key is down is one
  command, pauses included. Pressing it while Beacon is talking cuts it off.
- **"Hey Beacon"**: say the name and the command in one breath, or say the name, wait for the chime, then speak.
  The microphone listens all the time for the name while this is on. Switch it off in the menu to keep the
  microphone closed until you hold the key.

After it answers you have 8 seconds to follow up without the name or the key.

### While it works

Hold the key or say "Hey Beacon" in the middle of a task:

- **Ask**: "what are you seeing right now?", "how far along are you?". A fast model looks at the screen and the
  steps so far and answers in a sentence; the task keeps going.
- **Redirect**: "use Safari instead", "make it Saturday". It answers and the change reaches the agent at its next
  step.
- **Stop**: "stop", "stop, Beacon", "never mind, cancel that".

Before a task it says one short line of what it is about to do, written by the model, not a canned "On it".

## The menu bar

The bird at the top right shows what Beacon is doing:

| Glyph | Meaning |
|---|---|
| bird alone | Ready. |
| microphone | Hearing you. |
| dots, pulsing | Working, or starting up. |
| waveform | Speaking. |
| dimmed bird | Off. |

Click it for:

- **Listen for “Hey Beacon”**: the wake-word switch.
- **Push-to-Talk Key…**: press a new combination in the small window that opens. It needs ⌃, ⌥ or ⌘, or a
  function key on its own; Esc cancels.
- **Memory…**: see below.
- **Works in**: **My whole Mac** (the default), **My Chrome** (needs `extension_token`) or **Separate Chrome
  window** (its own profile). Switching restarts Beacon.
- **Turn Off / Turn On**: closes the microphone and the browser but keeps the icon.
- **Quit Beacon**: closes everything, as does Ctrl-C in the terminal.

The key, the wake-word switch and the mode are saved in `~/.beacon/settings.json`.

## Memory

**Memory…** in the menu opens a small window with two parts:

- **Notes**: plain text, saved as you type to `~/.beacon/memory.md`. Write who you are, how you like things done,
  facts for forms. Beacon follows the notes like instructions, and the greeting uses the name it finds there.
- **Files**: add with the button or drag them in. They are copied to `~/.beacon/memory/`. PDFs, images and text
  files are read; other files and anything over 10 MB are named so the agent knows they exist. Remove moves a
  file to the Trash.

Memory goes into the first message of each conversation, and editing it starts a fresh conversation. In the
browser modes the memory folder is also the only place uploads come from, so keep only files you are happy to
send. Never put passwords in memory.

## Voice

Beacon streams speech straight to the speakers from a voice service, so it starts talking a fraction of a second
after it has a line. Two are built in; pick one with `voice_service` in the config and set its key:

| `voice_service` | Key | Default voice and model |
|---|---|---|
| `"elevenlabs"` (default) | `ELEVENLABS_API_KEY` | "Vance", a casual American voice, `eleven_v4_turbo` |
| `"cartesia"` | `CARTESIA_API_KEY` (a standard key; admin keys cannot make speech) | "Daniel", `sonic-3.6` |

Set `voice` to any voice id from that service's library, and `voice_model` to change the model. Without the
chosen service's key Beacon uses macOS `say`. If the key is rejected or out of credits it says so in the
terminal and uses `say` for the rest of the run; a network error falls back for that line only.

## Work on your whole Mac

The default. Beacon sees your screen through screenshots and uses the real mouse and keyboard, with whatever you
are logged in to. "This" means what is in front of you.

- There is no spoken confirmation in this mode. It does what you ask, including sending, buying and deleting.
  Stop it by voice or the key.
- It works on the main display only, and it moves your real pointer, so keep your hands off while it works.
- Every step sends a screenshot of your screen to the Anthropic API, including anything visible on it.
- It is slower and costs more per step than the browser modes, because it reads pictures instead of page text.

## Work in a browser

**Works in > Separate Chrome window** drives a visible Chrome window with its own profile
(`~/.beacon/chrome-profile`). Log in to the sites you want it to use in that window once; the logins persist.
In the browser modes:

- It asks out loud before anything that submits, sends, pays, books, posts or deletes: a button or short link whose
  label says so, a form's own button labelled apply, complete, order, payment or booking, a button it can read no name
  for ("this button"), and Enter in a field that is not a search box. Only a short plain yes ("yes", "yeah", "sure", "go ahead", "do it")
  goes ahead; an unclear answer is asked once more, and silence means no.
- It is told never to type passwords, card numbers or verification codes and never to solve CAPTCHAs, and the
  gate refuses the fields it recognises as such (see Limits). It asks you to do those in the window, press the
  page's own sign-in or pay button yourself, and say "continue".
- When a command has a real fork and the choice is not cheaply undone, it asks which you mean. Otherwise it picks
  the likeliest and tells you which.
- Say "Hey Beacon, pause the video" while media plays: a follow-up without the name is not taken while a video or
  audio is playing, so its speech is not mistaken for a command.

### Summon it to the page you're on

In a browser mode, with a page open in your everyday Chrome and that Chrome in front, say "Hey Beacon, what is
this page about?". It opens the same address in its own window and works there. Your everyday
Chrome is only read, never controlled.

- The first time, macOS asks whether your terminal may control Google Chrome. Click Allow and say the command
  again. If you chose Don't Allow, switch Google Chrome on for your terminal in System Settings, Privacy &
  Security, Automation.
- Logins do not carry over. Its window has its own profile, so a page behind a login shows the login page until
  you have signed in there once.
- Google Chrome only, and only while it is the app in front. Other browsers, Chrome Beta and Canary, and
  incognito windows are not read.
- A PDF opens in its window, but it cannot read one.
- While your everyday Chrome is in front, every command sends the front tab's address and the first 200
  characters of its title to the Anthropic API, also when the command is about something else. For a site in
  `blocked_hosts` only the site's name is sent; for a local file, a `chrome://` page or another address it may
  not open, only the reason.

### Work in your own Chrome (opt-in)

To have it work in a tab of the Chrome you already use, with your logins:

1. Install the Playwright Extension from the Chrome Web Store:
   `https://chromewebstore.google.com/detail/playwright-extension/mmlmfjhmonkocbjadbfplnigmagldckm`
2. Click its toolbar icon and copy the value after `PLAYWRIGHT_MCP_EXTENSION_TOKEN=`.
3. Put `extension_token = "..."` in `~/.beacon/config.toml`, run `chmod 600 ~/.beacon/config.toml`, and pick
   **Works in > My Chrome**.

Beacon then opens one tab in your Chrome (in a "Playwright" tab group) and works there. Know what you are
trading:

- Every site you are logged in to is in reach, including email and banking. `blocked_hosts` and the spoken
  question are the only things between a hostile page and your accounts.
- The token lets any program on this Mac attach to your Chrome without asking. Rotate it on the extension's page
  if it leaks.
- Chrome shows a "started debugging this browser" banner while Beacon runs.
- Do not switch tabs in the window Beacon is working in; use another window. Each start leaves its tab behind.
- A wrong token fails after 30 seconds with a spoken error.

This mode passed the gate tests on a stand-in Chrome. It has not been tried on a real everyday profile.

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
| `menubar.py`, `menubar.swift`, `bird.png` | The menu bar icon, its menu, the push-to-talk key, the key recorder and the Memory window. macOS 26 does not show a menu bar item made by Python, so a small native helper shows it; it is compiled on first run and needs the Xcode command line tools (`xcode-select --install`). |
| `mac.py`, `mac_prompt.txt` | The whole-Mac mode: screenshots, mouse and keyboard through Anthropic's computer toolset. |
| `prompt.txt` | The system prompt for the browser modes. |

## Tests

```bash
uv run pytest                 # gate rules, real speech recognition on synthesized clips, real headless Chrome
RUN_LIVE=1 uv run pytest      # adds the live Claude and YouTube runs (a few cents)
```

No test needs a microphone or a person: clips are made with `say` and fed through a fake input device.

## Limits

- In the browser modes, the gate reads the labels the page wrote. A form's Next, Continue, Save or Sign in button is pressed without
  a question, also when it is the step that places an order, and a hostile page can label a pay button "Next".
  The prompt tells the model to ask you first when it suspects such a step; you watching the window is the last
  defence. Keep banking and your main email out of this profile.
- The gate cannot read text a page draws with CSS. A button named only that way is asked about as "this button"
  without its name, and a link named only that way is followed without a question.
- A field whose name, label or placeholder says "search" counts as a search box, so Enter in it is not asked
  about, even inside a longer form.
- A password, card or code field is recognised only by `type=password` or an `autocomplete` of
  `current-password`, `one-time-code` or `cc-...`. The prompt forbids typing passwords, card numbers and codes
  anywhere; the gate is the backstop, and a card or code box without those marks is an ordinary field to it.
- What you type into a field recognised that way is hidden from the page text sent to the API, but only for
  fields of the page itself: not inside embedded frames (most card forms), and not in the results of a search of
  the page. Press the page's own button before you say "continue", so nothing is left in the field.
- The gate does not question checkboxes and switches, dropdown choices, links with more than four words, buttons
  with only an icon or emoji as text, page keyboard shortcuts, or a form button labelled something like
  "Finish" or "Create account". The prompt tells the model to ask you first in those cases; code does not.
- It opens only plain `http://` and `https://` addresses: no user names, escapes or non-English letters in the
  site name. `localhost` and numeric addresses of this machine or your home network are refused; a site name
  that resolves to this machine is not checked.
- It cannot hear the wake word while it is speaking; hold the key to cut it off.
- Without the key, a pause longer than about a second in the middle of a command cuts the command off.
- A redirect reaches the agent after the step it is on, so one more action may happen first.
- The push-to-talk key is taken from every other app while Beacon runs.
- The prompt tells it not to automate LinkedIn (its terms forbid it). Nothing in code enforces that; add
  `linkedin.com` to `blocked_hosts` to make it a rule.
- A browser call that gets no answer for 45 seconds (a frozen page), or a browser server that dies, ends the
  program with one spoken line. Start it again.
- A command stops after 40 steps (60 on the whole Mac), and a single step that needs more than about 3,000 words of writing is cut off
  and reported as such.
