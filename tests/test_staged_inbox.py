"""A file from somewhere else can be sent into a chat or a forum topic.

POST /inbox takes the bytes and answers with a token; send_staged_file sends
that file as a document and throws it away.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from telegram_mcp.tools import staging


class Client:
    """Stands in for Telethon: remembers what it was asked to upload and send."""

    def __init__(self, fail=False, message_id=777):
        self.fail = fail
        self.message_id = message_id
        self.uploads = []
        self.sends = []

    async def upload_file(self, path, file_name=None):
        self.uploads.append((path, file_name, Path(path).read_bytes()))
        return SimpleNamespace(name=file_name)

    async def send_file(self, entity, handle, caption=None, reply_to=None, force_document=False):
        if self.fail:
            raise RuntimeError("flood wait")
        self.sends.append(
            {"entity": entity, "name": handle.name, "caption": caption, "reply_to": reply_to,
             "force_document": force_document}
        )
        return SimpleNamespace(id=self.message_id)


def _wire(monkeypatch, tmp_path, client):
    monkeypatch.setattr(staging, "get_client", lambda account=None: client)

    async def fake_entity(chat_id, cl=None):
        return SimpleNamespace(marker=chat_id)

    monkeypatch.setattr(staging, "resolve_entity", fake_entity)
    monkeypatch.setenv("MEDIA_STAGING_DIR", str(tmp_path / "staged"))
    staging._STAGED.clear()
    app = Starlette(routes=[Route("/inbox", staging.receive_inbox, methods=["POST"])])
    return TestClient(app)


def test_the_inbox_keeps_the_bytes_and_answers_with_a_token(monkeypatch, tmp_path):
    http = _wire(monkeypatch, tmp_path, Client())

    res = http.post("/inbox?filename=clip_01 main.mp4", content=b"finished clip")

    assert res.status_code == 200
    body = res.json()
    item = staging._STAGED[body["token"]]
    assert item["path"].read_bytes() == b"finished clip"
    assert body["size"] == len(b"finished clip")
    # The name on disk is the token, never anything the caller wrote.
    assert item["path"].name.startswith(f"inbox-{body['token']}")
    assert body["filename"] == "clip_01 main.mp4"


def test_a_path_in_the_filename_goes_nowhere(monkeypatch, tmp_path):
    http = _wire(monkeypatch, tmp_path, Client())

    body = http.post("/inbox?filename=../../etc/passwd", content=b"x").json()

    assert body["filename"] == "passwd"
    assert staging._STAGED[body["token"]]["path"].parent == tmp_path / "staged"


def test_a_file_over_the_limit_is_refused_and_nothing_is_kept(monkeypatch, tmp_path):
    http = _wire(monkeypatch, tmp_path, Client())
    monkeypatch.setattr(staging, "MAX_INBOX_BYTES", 10)

    res = http.post("/inbox?filename=big.mp4", content=b"x" * 11)

    assert res.status_code == 413
    assert staging._STAGED == {}
    assert list((tmp_path / "staged").iterdir()) == []


@pytest.mark.asyncio
async def test_a_file_is_sent_as_a_document_into_the_topic_and_then_deleted(monkeypatch, tmp_path):
    client = Client(message_id=4321)
    http = _wire(monkeypatch, tmp_path, client)
    token = http.post("/inbox?filename=main.mp4", content=b"clip").json()["token"]
    path = staging._STAGED[token]["path"]

    answer = json.loads(await staging.send_staged_file(chat_id=-1001, token=token, caption="Clip 1", reply_to=150))

    assert answer["message_id"] == 4321 and answer["sent"] is True
    assert client.uploads[0][1] == "main.mp4" and client.uploads[0][2] == b"clip"
    sent = client.sends[0]
    assert sent["reply_to"] == 150 and sent["force_document"] is True and sent["caption"] == "Clip 1"
    assert token not in staging._STAGED and not path.exists()


@pytest.mark.asyncio
async def test_a_failed_send_keeps_the_file_so_it_can_be_tried_again(monkeypatch, tmp_path):
    http = _wire(monkeypatch, tmp_path, Client(fail=True))
    token = http.post("/inbox?filename=main.mp4", content=b"clip").json()["token"]

    answer = await staging.send_staged_file(chat_id=-1001, token=token)

    assert "Error" in answer or "error" in answer
    assert token in staging._STAGED and staging._STAGED[token]["path"].exists()


@pytest.mark.asyncio
async def test_an_unknown_token_or_a_downloaded_file_cannot_be_sent(monkeypatch, tmp_path):
    client = Client()
    _wire(monkeypatch, tmp_path, client)
    assert await staging.send_staged_file(chat_id=-1001, token="nope") == "No inbox file with that token."

    # A file staged from Telegram for download is not an inbox file.
    downloaded = tmp_path / "staged" / "abc.mp4"
    downloaded.parent.mkdir(parents=True, exist_ok=True)
    downloaded.write_bytes(b"x")
    staging._STAGED["abc"] = {"path": downloaded, "expires": 9e18, "content_type": "video/mp4", "filename": "abc.mp4"}
    assert await staging.send_staged_file(chat_id=-1001, token="abc") == "No inbox file with that token."
    assert client.sends == []
