"""Beacon's hosted credit: without your own ANTHROPIC_API_KEY, Claude and the ElevenLabs voice go through Beacon's server,
which pays for them and allows each Mac $1 of Claude."""
import os
import uuid
from pathlib import Path

import anthropic
import httpx2
from anthropic import AsyncAnthropic

SERVER = "https://ikjddhlwksxxbefssfms.supabase.co/functions/v1/beacon"
CREDIT_REQUEST = f"{SERVER}/credit-request"
DEVICE_FILE = Path("~/.beacon/device").expanduser()
PAYMENT_REQUIRED = 402
ALREADY_REQUESTED = 409
REQUEST_TIMEOUT_SECONDS = 15


def device_id() -> str:
    """This Mac's random id, made on first use. It is what the server counts spend against."""
    if not DEVICE_FILE.exists():
        DEVICE_FILE.parent.mkdir(parents=True, exist_ok=True)
        DEVICE_FILE.write_text(str(uuid.uuid4()))
        DEVICE_FILE.chmod(0o600)
    return DEVICE_FILE.read_text().strip()


def hosted() -> bool:
    return not os.environ.get("ANTHROPIC_API_KEY")


def claude(**options) -> AsyncAnthropic:
    """Your own key when ANTHROPIC_API_KEY is set, otherwise Beacon's server."""
    return AsyncAnthropic(api_key=device_id(), base_url=SERVER, **options) if hosted() else AsyncAnthropic(**options)


def out_of_credit(error: BaseException) -> bool:
    return isinstance(error, anthropic.APIStatusError) and error.status_code == PAYMENT_REQUIRED


async def ask_for_more() -> bool:
    """Email Beacon's owner that this Mac wants more credit. False when it already asked; raises on any other failure."""
    async with httpx2.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as http:
        answer = await http.post(CREDIT_REQUEST, headers={"x-api-key": device_id()})
    if answer.status_code == ALREADY_REQUESTED:
        return False
    answer.raise_for_status()
    return True
