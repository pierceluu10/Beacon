"""The page the user is looking at in their everyday Chrome, read through Apple Events. Read-only."""
import asyncio
from asyncio.subprocess import PIPE
from pathlib import Path

FRONT_TAB_SCRIPT = Path(__file__).with_name("front_tab.js")
FRONT_TAB_SECONDS = 1.0  # a healthy read takes 0.13 s; an unanswered macOS permission prompt blocks for as long as it is up
NOT_PERMITTED = b"-1743"  # errAEEventNotPermitted, thrown by front_tab.js
NOT_ALLOWED = ("macOS is not letting you see the user's everyday browser. If the command needs that page, tell them to switch on "
               "Google Chrome for their terminal in System Settings, Privacy and Security, Automation.")
NO_ANSWER = ("The user's everyday browser did not answer, most likely because macOS is showing a prompt asking to control "
             "Google Chrome. If the command needs that page, tell them to click Allow and ask again.")


class EverydayBrowserError(Exception):
    """The everyday browser is in front but cannot be read. The message is written for the model."""


async def front_tab(agent_profile: Path) -> tuple[str, str] | None:
    """(address, title) of the front tab when the app in front is a Chrome other than the agent's own, else None."""
    process = await asyncio.create_subprocess_exec("osascript", "-l", "JavaScript", str(FRONT_TAB_SCRIPT), str(agent_profile),
                                                   stdout=PIPE, stderr=PIPE)
    try:
        async with asyncio.timeout(FRONT_TAB_SECONDS):
            output, error = await process.communicate()
    except TimeoutError:
        raise EverydayBrowserError(NO_ANSWER) from None
    finally:
        if process.returncode is None:  # timed out or cancelled by "stop"
            process.kill()
            await process.wait()
    if NOT_PERMITTED in error:
        raise EverydayBrowserError(NOT_ALLOWED)
    address, _, title = output.decode().strip().partition("\n")
    return (address, title) if address else None
