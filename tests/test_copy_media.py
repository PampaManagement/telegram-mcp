"""copy_media re-sends a file by reference: no download, no forward header."""

import json
from types import SimpleNamespace

import pytest

from telegram_mcp.tools import media


class RecordingClient:
    def __init__(self, with_media=True):
        self.sent = []
        self.downloads = []
        self.message = SimpleNamespace(
            id=500, media=SimpleNamespace(kind="document") if with_media else None
        )

    async def get_messages(self, entity, ids=None):
        self.asked = (entity, ids)
        return self.message

    async def send_file(self, entity, file, caption=None, reply_to=None):
        self.sent.append({"entity": entity, "file": file, "caption": caption, "reply_to": reply_to})
        return SimpleNamespace(id=900 + len(self.sent))

    async def download_media(self, *args, **kwargs):  # pragma: no cover - must never run
        self.downloads.append(args)
        raise AssertionError("copy_media must never download the file")


def _wire(monkeypatch, client):
    monkeypatch.setattr(media, "clients", {"default": client})
    monkeypatch.setattr(media, "get_client", lambda account=None: client)

    async def fake_resolve(chat_id, cl=None):
        return SimpleNamespace(marker=chat_id)

    monkeypatch.setattr(media, "resolve_entity", fake_resolve)


@pytest.mark.asyncio
async def test_copy_media_reuses_the_file_and_sets_its_own_caption(monkeypatch):
    client = RecordingClient()
    _wire(monkeypatch, client)

    result = await media.copy_media(
        from_chat_id=-1001, message_id=42, to_chat_id=-1002, caption="Felicity\nbeach clip"
    )

    assert client.downloads == [], "nothing is downloaded"
    assert len(client.sent) == 1
    call = client.sent[0]
    assert call["file"] is client.message.media, "the existing file reference is re-used"
    assert call["caption"] == "Felicity\nbeach clip"
    assert call["reply_to"] is None
    assert call["entity"].marker == -1002
    body = json.loads(result)
    assert body["sent"] is True
    assert body["message_id"] == 901


@pytest.mark.asyncio
async def test_copy_media_into_a_topic(monkeypatch):
    client = RecordingClient()
    _wire(monkeypatch, client)

    result = await media.copy_media(
        from_chat_id=-1001, message_id=42, to_chat_id=-1002, caption="x", topic_id=41
    )

    assert client.sent[0]["reply_to"] == 41
    assert json.loads(result)["topic_id"] == 41


@pytest.mark.asyncio
async def test_copy_media_says_so_when_there_is_no_media(monkeypatch):
    client = RecordingClient(with_media=False)
    _wire(monkeypatch, client)

    result = await media.copy_media(from_chat_id=-1001, message_id=42, to_chat_id=-1002)

    assert "No media" in result
    assert client.sent == []
