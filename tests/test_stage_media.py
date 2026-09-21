"""A file in Telegram can be handed to something that is not on this machine."""

import json
import re
from types import SimpleNamespace

import pytest

from telegram_mcp.tools import staging


class Client:
    """Stands in for Telethon: writes a file where it is told to."""

    def __init__(self, body=b"clip bytes", suffix=".mp4", media=True):
        self.body = body
        self.suffix = suffix
        self.media = media
        self.asked = []

    async def get_messages(self, entity, ids=None):
        self.asked.append(ids)
        return SimpleNamespace(
            media=SimpleNamespace(marker=1) if self.media else None,
            document=SimpleNamespace(mime_type="video/mp4"),
        )

    async def download_media(self, msg, file=None):
        path = f"{file}{self.suffix}"
        with open(path, "wb") as handle:
            handle.write(self.body)
        return path


def _wire(monkeypatch, tmp_path, client, base=None):
    monkeypatch.setattr(staging, "get_client", lambda account=None: client)

    async def fake_entity(chat_id, cl=None):
        return SimpleNamespace(marker=chat_id)

    monkeypatch.setattr(staging, "resolve_entity", fake_entity)
    monkeypatch.setenv("MEDIA_STAGING_DIR", str(tmp_path / "staged"))
    if base is None:
        monkeypatch.delenv("MEDIA_PUBLIC_BASE_URL", raising=False)
        monkeypatch.delenv("RAILWAY_PUBLIC_DOMAIN", raising=False)
    else:
        monkeypatch.setenv("MEDIA_PUBLIC_BASE_URL", base)
    staging._STAGED.clear()


@pytest.mark.asyncio
async def test_a_staged_file_answers_with_a_path_and_its_type(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, Client())

    body = json.loads(await staging.stage_media(chat_id=-1001, message_id=42))

    assert re.fullmatch(r"/staged/[a-f0-9]{32}", body["path"])
    assert body["content_type"] == "video/mp4"
    assert body["size"] == len(b"clip bytes")
    assert "expires_at" in body


@pytest.mark.asyncio
async def test_no_url_is_built_when_the_only_way_in_carries_a_secret(monkeypatch, tmp_path):
    """A link built from the gateway secret would hand the secret to whoever gets the link."""
    _wire(monkeypatch, tmp_path, Client())

    body = json.loads(await staging.stage_media(chat_id=-1001, message_id=42))

    assert "url" not in body


@pytest.mark.asyncio
async def test_a_url_is_built_when_this_server_is_reachable_on_its_own(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, Client(), base="https://media.example.test/")

    body = json.loads(await staging.stage_media(chat_id=-1001, message_id=42))

    assert body["url"] == f"https://media.example.test{body['path']}"


@pytest.mark.asyncio
async def test_a_message_with_no_media_says_so(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, Client(media=False))

    answer = await staging.stage_media(chat_id=-1001, message_id=42)

    assert answer == "No media found in the specified message."


@pytest.mark.asyncio
async def test_the_token_is_the_key_and_it_expires(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, Client())
    body = json.loads(await staging.stage_media(chat_id=-1001, message_id=42, ttl_minutes=1))
    token = body["path"].rsplit("/", 1)[1]

    assert token in staging._STAGED
    path = staging._STAGED[token]["path"]
    assert path.exists()

    # Past its expiry it is forgotten and the file is gone, so the link is dead.
    staging._STAGED[token]["expires"] = 0
    staging._sweep()
    assert token not in staging._STAGED
    assert not path.exists()


@pytest.mark.asyncio
async def test_a_staged_file_can_be_thrown_away_early(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, Client())
    body = json.loads(await staging.stage_media(chat_id=-1001, message_id=42))
    token = body["path"].rsplit("/", 1)[1]
    path = staging._STAGED[token]["path"]

    assert await staging.unstage_media(token=token) == "Staged file removed."
    assert not path.exists()
    assert await staging.unstage_media(token=token) == "No staged file with that token."


@pytest.mark.asyncio
async def test_every_staging_gets_its_own_token(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, Client())

    first = json.loads(await staging.stage_media(chat_id=-1001, message_id=42))
    second = json.loads(await staging.stage_media(chat_id=-1001, message_id=42))

    assert first["path"] != second["path"]


@pytest.mark.asyncio
async def test_a_silly_ttl_is_brought_back_into_range(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, Client())

    body = json.loads(await staging.stage_media(chat_id=-1001, message_id=42, ttl_minutes=10_000))
    token = body["path"].rsplit("/", 1)[1]
    life = staging._STAGED[token]["expires"] - __import__("time").time()

    assert life <= staging.MAX_TTL_MINUTES * 60 + 5
