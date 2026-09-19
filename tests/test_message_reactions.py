"""A message nobody has reacted to answers "none yet", not an error."""

import json
from types import SimpleNamespace

import pytest
from telethon.errors.rpcerrorlist import MsgIdInvalidError

from telegram_mcp import runtime
from telegram_mcp.tools import messages


def _msg_id_invalid():
    try:
        return MsgIdInvalidError(request=None)
    except TypeError:
        return MsgIdInvalidError(None)


class Client:
    def __init__(self, result):
        self.result = result

    async def __call__(self, request):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _wire(monkeypatch, client):
    monkeypatch.setattr(messages, "clients", {"default": client})
    monkeypatch.setattr(messages, "get_client", lambda account=None: client)

    async def fake_peer(chat_id, cl=None):
        return SimpleNamespace(marker=chat_id)

    monkeypatch.setattr(messages, "resolve_input_entity", fake_peer)


@pytest.mark.asyncio
async def test_no_reactions_reads_as_an_empty_list(monkeypatch):
    _wire(monkeypatch, Client(_msg_id_invalid()))

    body = json.loads(await messages.get_message_reactions(chat_id=-1001, message_id=42))

    assert body == {"message_id": 42, "chat_id": "-1001", "reactions": [], "count": 0}


@pytest.mark.asyncio
async def test_an_empty_reactions_list_reads_the_same_way(monkeypatch):
    _wire(monkeypatch, Client(SimpleNamespace(reactions=[])))

    body = json.loads(await messages.get_message_reactions(chat_id=-1001, message_id=42))

    assert body["reactions"] == []
    assert body["count"] == 0


@pytest.mark.asyncio
async def test_reactions_are_returned_with_who_and_what(monkeypatch):
    from telethon.tl.types import ReactionEmoji

    reaction = SimpleNamespace(
        peer_id=SimpleNamespace(user_id=8548207586),
        reaction=ReactionEmoji(emoticon="👍"),
        date=None,
    )
    _wire(monkeypatch, Client(SimpleNamespace(reactions=[reaction])))

    body = json.loads(await messages.get_message_reactions(chat_id=-1001, message_id=42))

    assert body["count"] == 1
    assert body["reactions"][0]["user_id"] == 8548207586
    assert body["reactions"][0]["emoji"] == "👍"


def test_an_error_code_is_the_same_every_time():
    first = runtime.log_and_format_error("get_message_reactions", ValueError("boom"))
    second = runtime.log_and_format_error("get_message_reactions", RuntimeError("different"))
    assert "GEN-ERR-" in first
    # Same function, same code, whatever the error and whatever the process:
    # the code names the call, so it can be matched on and looked up later.
    assert first == second
    other = runtime.log_and_format_error("send_message", ValueError("boom"))
    assert other != first
