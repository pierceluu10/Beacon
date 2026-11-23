"""Beacon's hosted credit on this Mac: the device id, which key a request carries, and the one email for more credit.
No request leaves the machine."""
import asyncio
from pathlib import Path

import httpx2
import pytest

import credit


@pytest.fixture(autouse=True)
def device_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "beacon" / "device"
    monkeypatch.setattr(credit, "DEVICE_FILE", path)
    return path


def test_the_device_id_is_made_once_and_kept_private(device_file: Path) -> None:
    first = credit.device_id()
    assert credit.device_id() == first and device_file.read_text() == first
    assert device_file.stat().st_mode & 0o777 == 0o600, oct(device_file.stat().st_mode)


def test_without_a_key_claude_goes_through_beacons_server_as_this_mac(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = credit.claude()
    assert str(client.base_url).rstrip("/") == credit.SERVER and client.api_key == credit.device_id()


def test_your_own_key_goes_straight_to_anthropic(monkeypatch: pytest.MonkeyPatch, device_file: Path) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-own")
    client = credit.claude()
    assert client.api_key == "sk-ant-own" and "anthropic.com" in str(client.base_url)
    assert not device_file.exists(), "a Mac with its own key never registers with Beacon's server"


@pytest.mark.parametrize(("status", "outcome"), [(200, True), (409, False)], ids=["sent", "already asked"])
def test_asking_for_more_credit(monkeypatch: pytest.MonkeyPatch, status: int, outcome: bool) -> None:
    sent = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return httpx2.Response(status, json={})

    real_client = httpx2.AsyncClient
    monkeypatch.setattr(credit.httpx2, "AsyncClient",
                        lambda **options: real_client(transport=httpx2.MockTransport(handler), **options))
    assert asyncio.run(credit.ask_for_more()) is outcome
    assert str(sent[0].url) == credit.CREDIT_REQUEST and sent[0].headers["x-api-key"] == credit.device_id()


def test_a_refused_credit_request_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    real_client = httpx2.AsyncClient
    monkeypatch.setattr(credit.httpx2, "AsyncClient", lambda **options: real_client(
        transport=httpx2.MockTransport(lambda request: httpx2.Response(503, json={})), **options))
    with pytest.raises(httpx2.HTTPStatusError):
        asyncio.run(credit.ask_for_more())
