# Contributing to Beacon

Thanks for helping. Beacon acts on people's real accounts, so changes are judged first on whether they keep it
safe, then on whether they keep it small.

## Setup

You need macOS on Apple silicon, Python 3.12, [uv](https://docs.astral.sh/uv/), Chrome, and the Xcode command
line tools (`xcode-select --install`, for the menu bar helper).

```bash
git clone https://github.com/pierceluu10/Beacon.git && cd Beacon
uv sync
uv run python main.py       # runs on this Mac's free $1; export ANTHROPIC_API_KEY to use your own
```

## Tests

```bash
uv run pytest                                           # everything in Python, free
node --test supabase/functions/beacon/credit.test.ts    # the server's credit rules
```

The tests cost nothing and need no key, microphone or person: no test calls Claude or ElevenLabs, speech clips are
made with `say` and fed through a fake input device ([`tests/fake_microphone.py`](tests/fake_microphone.py)), and
browser tests drive headless Chrome on pages in [`tests/site/`](tests/site). Run both before you open a pull request.

## Making a change

- **Read the code you touch first** and reuse what is there; the file table in the README says where things live.
- **Safety changes need a test.** A change to [`gate.py`](gate.py) or [`inspect.js`](inspect.js) adds a case to
  [`tests/test_gate.py`](tests/test_gate.py) (and a page in `tests/site/` when it needs real DOM) showing what is
  now asked about or refused. Never loosen a rule without saying why in the pull request.
- **Keep it small.** No new dependency for a few lines, no options nobody asked for, no dead code.
- **Name constants** instead of bare numbers and strings, and comment only the why the code cannot show.
- **Never commit keys.** They are read from the environment; keep them in your shell profile.

## Pull requests

1. Branch from `main` (`feat/<short-name>`, `fix/<short-name>`).
2. Use [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`, `test:`,
   `refactor:`, `chore:`.
3. Keep the description short: what changed, why, and how you tested it (say if you tried it by voice on a real Mac).
4. Update the README when behaviour, configuration or a limitation changes.

## Reporting bugs and security issues

Open an issue with what you said, what Beacon did, and the terminal output. For a way to make Beacon act without
the spoken yes it should ask for, or to leak a secret, please open a private
[security advisory](https://github.com/pierceluu10/Beacon/security/advisories/new) instead of a public issue.
