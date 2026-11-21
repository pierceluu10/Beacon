<p align="center">
  <img src="logo.png" width="96" alt="Beacon logo: a small bird in a white rounded square">
</p>
<h1 align="center"><code>beacon</code></h1>
<p align="center"><b>Say its name, speak a command, watch Chrome do it.</b></p>
<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT license"></a>
  <img src="https://img.shields.io/badge/macOS-Apple%20silicon-lightgrey" alt="macOS on Apple silicon">
  <img src="https://img.shields.io/badge/python-3.12-blue" alt="Python 3.12">
</p>

Beacon is a voice assistant that drives a real, visible Chrome window. Say "Hey Beacon", speak a command, and watch
it navigate, type and click. Speech runs on your Mac; the only paid service is the Anthropic API.

```
You:    "Hey Beacon."
Beacon: "Hi Pierce."
You:    "Go to YouTube and search for lo-fi hip hop radio."
Beacon: "On it."            (Chrome navigates, types and clicks while you watch)
Beacon: "The results are up. The first one is a live stream."
```

Speech recognition and speech output run on this Mac. The only paid service is the Anthropic API.

## Run it

```bash
export ANTHROPIC_API_KEY=...       # if it is not already in your shell profile
uv sync
uv run python main.py
```

The first run asks for microphone access for your terminal and opens a Chrome window with its own profile
(`~/.beacon/chrome-profile`). Log in to the sites you want it to use in that window once; the logins persist.
When you hear "Ready", say "Hey Beacon".

A bird icon appears in the menu bar at the top right while it runs. Click it for:

- **Mute**: stops listening and releases the microphone, so the orange indicator in the menu bar goes off; a running task cannot be stopped by voice.
- **Turn Off / Turn On**: closes the browser and the microphone but keeps the icon, so you can start it again.
- **Works in**: **My whole Mac**, **My Chrome** (needs `extension_token`) or **Separate Chrome window** (its own
  profile). Switching restarts Beacon; the choice is not saved, and it starts in the `mode` from your config.
- **Quit Beacon**: closes everything and exits, as does Ctrl-C.

The small text beside the bird says `…` while starting, `muted` or `off`.

- Say the name and the command in one breath, or say the name, wait for "Hi Pierce", then speak.
- After it answers you have 8 seconds to give a follow-up without the name, unless a video or audio is playing:
  then say the name again, so the video's speech is not taken as a command.
- Say "stop" while it is working, or as the answer to one of its questions, to cancel; "Stop, Beacon" and
  "Stop! Stop!" work too. "Thanks" or silence ends the exchange, and so does "stop" when nothing is running: to
  stop a video, say "Hey Beacon, pause the video".
- It asks out loud before anything that submits, sends, pays, books, posts or deletes: a button or short link whose
  label says so, a form's own button labelled apply, complete, order, payment or booking, a button it can read no name
  for ("this button"), and Enter in a field that is not a search box. Only a short plain yes ("yes", "yeah", "sure", "go ahead", "do it")
  goes ahead; an unclear answer is asked once more, and silence means no.
- It is told never to type passwords, card numbers or verification codes and never to solve CAPTCHAs, and the
  gate refuses the fields it recognises as such (see Limits). It asks you to do those in the window, press the
  page's own sign-in or pay button yourself, and say "continue".
- When a command has a real fork (two postings with the same title, two saved addresses) and the choice is not
  cheaply undone, it asks which you mean. Otherwise it picks the likeliest and tells you which. It asks one
  question at a time.
- It knows the local date and time, so it answers those without opening a site.
- Ctrl-C in the terminal quits, also in the middle of a task. When a spoken error says the reason is in the
  terminal, it is printed there; API errors during a task are printed too.

## Job applications

1. Put your resume at `~/.beacon/uploads/resume.pdf`. Every file in that folder can be attached by the agent, so
   keep only files you are happy to send.
2. Write `~/.beacon/facts.md` in plain text: name, email, phone, links, location, work authorization, notice
   period, salary expectations. It answers form questions only from this file and what you say; anything
   missing, it asks you. No passwords.
3. Open the job posting in your own Chrome or in its window (or tell it the address) and say "Hey Beacon, apply to
   this job with my resume".

## Summon it to the page you're on

With a page open in your everyday Chrome and that Chrome in front, say "Hey Beacon, apply to this job" or "Hey
Beacon, what is this page about?". It opens the same address in its own window and works there. Your everyday
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

## Work on your whole Mac

Set `mode = "mac"` in `~/.beacon/config.toml`, or pick **Works in > My whole Mac** from the bird menu. Beacon then
sees your screen through screenshots and uses the real mouse and keyboard, in any app, with whatever you are
logged in to. "This" means what is in front of you.

- macOS asks twice for your terminal app: **Screen Recording** and **Accessibility** (System Settings, Privacy and
  Security). Allow both, quit and reopen the terminal, and start Beacon again.
- There is no spoken confirmation in this mode. It does what you ask, including sending, buying and deleting.
  Say "stop" to cancel a running task; it cannot hear you while it is speaking.
- It works on the main display only, and it moves your real pointer, so keep your hands off while it works.
- Every step sends a screenshot of your screen to the Anthropic API, including anything visible on it.
- It is slower and costs more per step than the browser modes, because it reads pictures instead of page text.

## Work in your own Chrome (opt-in)

By default Beacon drives its own Chrome window with its own logins. To have it work in a tab of the Chrome you
already use, with your logins:

1. Install the Playwright Extension from the Chrome Web Store:
   `https://chromewebstore.google.com/detail/playwright-extension/mmlmfjhmonkocbjadbfplnigmagldckm`
2. Click its toolbar icon and copy the value after `PLAYWRIGHT_MCP_EXTENSION_TOKEN=`.
3. Put `extension_token = "..."` and `mode = "chrome"` in `~/.beacon/config.toml`, then `chmod 600 ~/.beacon/config.toml`.

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

Optional, in `~/.beacon/config.toml`. Unknown keys are an error.

| Key | Default | Meaning |
|---|---|---|
| `agent_name` | `"Beacon"` | The wake name, one word. Any name works; no training. |
| `wake_aliases` | `[]` | Other spellings the recogniser produces for your name. |
| `user_name` | `"Pierce"` | Used in the greeting. |
| `model` | `"claude-opus-5-5"` | Any Anthropic model id. The tests run on the default; `claude-sonnet-5-5` was tried by hand on multi-step tasks. |
| `profile_dir` | `"~/.beacon/chrome-profile"` | The agent's own Chrome profile. |
| `resume_path` | `"~/.beacon/uploads/resume.pdf"` | "My resume". Its folder is the only place uploads can come from. |
| `facts_path` | `"~/.beacon/facts.md"` | Facts about you for forms. |
| `blocked_hosts` | `[]` | Sites it may never navigate to, for example your bank. Links and redirects that land there are not checked. |
| `stt_model` | `"MEDIUM_STREAMING"` | Or `"SMALL_STREAMING"` (less memory, slightly less accurate). |
| `say_voice` | `"Samantha"` | Any voice from `say -v '?'`. |
| `mode` | `"window"` | What it works in: `"window"` (its own Chrome window), `"chrome"` (your Chrome, needs `extension_token`) or `"mac"` (your whole Mac). |
| `extension_token` | `""` | Set it to work in your own Chrome through the Playwright Extension (see above). |

## How it works

| File | Job |
|---|---|
| `main.py` | Configuration and the conversation loop. |
| `voice.py` | Moonshine speech-to-text on the microphone, macOS `say`, and wake / yes / no / stop parsing. |
| `agent.py` | Claude driving Chrome through the Playwright MCP server. |
| `gate.py` | The rules for what needs a spoken yes and what is refused. |
| `inspect.js` | Runs in the page to describe the element a click or key press would hit, so the gate judges the real DOM, not the model's description. |
| `everyday.py` | Reads the address and title of the front tab of your everyday Chrome when that Chrome is the app in front. |
| `front_tab.js` | The macOS script `everyday.py` runs. It addresses the Chrome in front by process id, because every Chrome answers to the same name. |
| `menubar.py`, `menubar.swift`, `bird.png` | The menu bar icon and its menu. macOS 26 does not show a menu bar item made by Python, so a small native helper shows it; it is compiled on first run and needs the Xcode command line tools (`xcode-select --install`). |
| `mac.py`, `mac_prompt.txt` | The whole-Mac mode: screenshots, mouse and keyboard through Anthropic's computer toolset. |
| `prompt.txt` | The system prompt. |

## Tests

```bash
uv run pytest                 # gate rules, real speech recognition on synthesized clips, real headless Chrome
RUN_LIVE=1 uv run pytest      # adds the live Claude and YouTube runs (a few cents)
```

No test needs a microphone or a person: clips are made with `say` and fed through a fake input device.

## Limits

- The gate reads the labels the page wrote. A form's Next, Continue, Save or Sign in button is pressed without
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
- It cannot hear you while it is speaking. After a bare "Hey Beacon", say the command in the same breath or wait
  for "Hi Pierce": a command begun a second or two after the name is talked over, and only its end is acted on.
- A pause longer than about a second in the middle of a command cuts the command off.
- The prompt tells it not to automate LinkedIn (its terms forbid it). Nothing in code enforces that; add
  `linkedin.com` to `blocked_hosts` to make it a rule.
- A browser call that gets no answer for 45 seconds (a frozen page), or a browser server that dies, ends the
  program with one spoken line. Start it again.
- A command stops after 40 steps, and a single step that needs more than about 3,000 words of writing is cut off
  and reported as such.
