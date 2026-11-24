<p align="center">
  <img src="logo.png" width="96" alt="Beacon logo: a white pointer cut out of an amber light on a dark tile">
</p>
<h1 align="center"><code>beacon</code></h1>
<p align="center"><b>Talk to your Mac. Watch it do the work.</b></p>
<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT license"></a>
  <img src="https://img.shields.io/badge/macOS-Apple%20silicon-lightgrey" alt="macOS on Apple silicon">
  <img src="https://img.shields.io/badge/python-3.12-blue" alt="Python 3.12">
</p>
<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#configuration">Configuration</a> ·
  <a href="#security">Security</a> ·
  <a href="CONTRIBUTING.md">Contributing</a>
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

## Features

- **Any app, real input**: screenshots plus the real mouse and keyboard, or a Chrome window it drives directly.
- **Talk over it**: ask, redirect or stop mid-task; it listens while it works and while it speaks.
- **Spoken yes before anything irreversible** in the browser modes, checked in code against the real page.
- **Local speech recognition** with Moonshine; nothing you say leaves the Mac except the command text.
- **Memory**: notes and files about you, read at the start of every conversation.
- **Native menu bar item** with a push-to-talk key recorder and a wake-word switch.

## Requirements

- macOS on Apple silicon, with the Xcode command line tools (`xcode-select --install`)
- Python 3.12 and [uv](https://docs.astral.sh/uv/)

No API keys needed.

## Quick start

```bash
git clone https://github.com/pierceluu10/Beacon.git && cd Beacon
uv sync
uv run python main.py
```

On first launch Beacon asks for everything it needs at once: the microphone, Screen Recording and Accessibility,
all for the terminal you ran it from. Allow each one, quit and reopen the terminal (macOS applies Screen Recording
only after a restart), and start Beacon again. Then **hold ⌥Space** (default key) and talk.

## Usage

### Talking to it

- **Push to talk**: hold ⌥Space (the default; change it in the menu), speak, let go. Pressing it while Beacon talks cuts it off.
- **"Hey Beacon"**: say the name and the command, or the name, wait for the chime, then speak. Works while it is
  talking too. Turn it off in the menu to keep the microphone closed until you hold the key.

After an answer you have 8 seconds to follow up without either. Mid-task you can **ask** ("what are you
seeing?"), **redirect** ("make it Saturday") or **stop** ("stop", "never mind").

### The menu bar

The mark shows a microphone while it hears you, pulsing dots while it works, a waveform while it talks, and dims
when off. The menu holds the wake-word switch, the push-to-talk key recorder, Memory, where it works (**My whole
Mac**, **My Chrome** or **Separate Chrome window**), Turn Off and Quit. Choices are saved in
`~/.beacon/settings.json`.

### Memory

**Memory…** opens notes (`~/.beacon/memory.md`: who you are, how you like things done) and files
(`~/.beacon/memory/`: PDFs, images and text are read). Both go into each new conversation. In the browser modes the
memory folder is the only place uploads come from. Never put passwords in memory.

### Where it works

- **Whole Mac** (default): screenshots plus the real mouse and keyboard, in any app, main display only. No spoken
  confirmation: it does what you ask, including sending and buying. Every step sends a screenshot to Claude.
- **Separate Chrome window**: its own profile at `~/.beacon/chrome-profile`; log in to sites there once. With your
  everyday Chrome in front, "Hey Beacon, what is this page about?" opens the same address in its window.
- **My Chrome** (opt-in): a tab in the Chrome you already use, with every login in reach. Install the
  [Playwright Extension](https://chromewebstore.google.com/detail/playwright-extension/mmlmfjhmonkocbjadbfplnigmagldckm),
  put its token in `~/.beacon/config.toml` as `extension_token = "..."`, and `chmod 600` that file. The token lets any program
  on this Mac attach to your Chrome, so rotate it if it leaks.

## Configuration

Optional, in `~/.beacon/config.toml`:

| Key | Meaning |
|---|---|
| `extension_token` | For **My Chrome** (above). |

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
  `linkedin.com` is always blocked (`ALWAYS_BLOCKED_HOSTS` in [`main.py`](main.py)). A link or redirect that lands
  on a blocked site closes the page (`LANDED_BLOCKED` in [`agent.py`](agent.py)).
- **Files stay put.** Uploads come only from the memory folder (the browser server's working directory in
  [`agent.py`](agent.py)), and saving pages to files is refused (`FILE_REFUSAL`).
- **Speech stays local.** Recognition runs on this Mac with Moonshine ([`voice.py`](voice.py)); only the text of
  what you said reaches Claude.
- **No keys on your Mac.** The Claude and ElevenLabs keys live on Beacon's server
  ([`supabase/functions/beacon`](supabase/functions/beacon)), which knows each Mac only by a random id
  (`~/.beacon/device`, [`credit.py`](credit.py)), allows only Beacon's two models, caps each request, and stops at
  $1 per Mac.

## Architecture

| File | Job |
|---|---|
| `main.py` | Configuration and the conversation loop, including listening while a task runs. |
| `credit.py` | The free credit: this Mac's id, Claude through Beacon's server, and the request for more. |
| `supabase/functions/beacon` | Beacon's server: holds the keys, counts each Mac's Claude spend and ElevenLabs characters, records requests for more credit. |
| `voice.py` | Moonshine speech-to-text on the microphone, push-to-talk, and wake / yes / no / stop parsing. |
| `mouth.py` | Speech out: the ElevenLabs voice through Beacon's server, or macOS `say` if that fails. |
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

## Contributing

Issues and pull requests are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) for the dev setup, the test suite
and what a good pull request looks like. Anything that touches [`gate.py`](gate.py) or [`inspect.js`](inspect.js)
needs a test that shows the action it now asks about or refuses.

## License

[MIT](LICENSE)
