"""A forward can be placed in a forum topic of the destination supergroup."""

from types import SimpleNamespace

import pytest
from telethon.tl import functions

from telegram_mcp.tools import messages


class RecordingClient:
    def __init__(self):
        self.requests = []
        self.helper_calls = []

    async def __call__(self, request):
        self.requests.append(request)
        return SimpleNamespace(updates=[])

    async def forward_messages(self, to_entity, ids, from_entity, **kwargs):
        self.helper_calls.append((to_entity, ids, from_entity, kwargs))
        return []


def _wire(monkeypatch, client):
    monkeypatch.setattr(messages, "clients", {"default": client})
    monkeypatch.setattr(messages, "get_client", lambda account=None: client)

    async def fake_resolve(chat_id, cl):
        return SimpleNamespace(marker=chat_id)

    monkeypatch.setattr(messages, "resolve_entity", fake_resolve)


@pytest.mark.asyncio
async def test_forward_into_topic_uses_raw_request_with_top_msg_id(monkeypatch):
    client = RecordingClient()
    _wire(monkeypatch, client)

    result = await messages.forward_message(
        from_chat_id=-1001, message_id=42, to_chat_id=-1002, expand_album=False, topic_id=560
    )

    assert client.helper_calls == []
    assert len(client.requests) == 1
    request = client.requests[0]
    assert isinstance(request, functions.messages.ForwardMessagesRequest)
    assert request.id == [42]
    assert request.top_msg_id == 560
    assert request.from_peer.marker == -1001
    assert request.to_peer.marker == -1002
    assert len(request.random_id) == 1
    assert request.drop_media_captions is None
    assert "topic 560" in result


@pytest.mark.asyncio
async def test_forward_without_topic_keeps_the_helper(monkeypatch):
    client = RecordingClient()
    _wire(monkeypatch, client)

    result = await messages.forward_message(
        from_chat_id=-1001, message_id=42, to_chat_id=-1002, expand_album=False
    )

    assert client.requests == []
    assert len(client.helper_calls) == 1
    assert client.helper_calls[0][1] == 42
    assert client.helper_calls[0][3] == {}
    assert "topic" not in result


@pytest.mark.asyncio
async def test_drop_captions_in_both_paths(monkeypatch):
    client = RecordingClient()
    _wire(monkeypatch, client)

    await messages.forward_message(
        from_chat_id=-1001, message_id=42, to_chat_id=-1002, expand_album=False,
        topic_id=560, drop_captions=True,
    )
    assert client.requests[0].drop_media_captions is True

    await messages.forward_message(
        from_chat_id=-1001, message_id=43, to_chat_id=-1002, expand_album=False,
        drop_captions=True,
    )
    assert client.helper_calls[0][3] == {"drop_media_captions": True}


@pytest.mark.asyncio
async def test_batch_forward_into_topic(monkeypatch):
    client = RecordingClient()
    _wire(monkeypatch, client)

    result = await messages.forward_messages(
        from_chat_id=-1001, message_ids=[7, 8, 9], to_chat_id=-1002, topic_id=12
    )

    request = client.requests[0]
    assert request.id == [7, 8, 9]
    assert request.top_msg_id == 12
    assert len(request.random_id) == 3
    assert "topic 12" in result
