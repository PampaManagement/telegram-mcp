"""Handing a Telegram file to something that lives outside Telegram.

`download_media` writes to this container's own disk and answers with a path.
That is all a caller on the same machine needs, and no use at all to a service
somewhere else: nothing the MCP protocol returns carries bytes.

`stage_media` closes that gap. It downloads the file once into a staging
directory and answers with the path this server will serve it back on, good
for a couple of hours. The caller already knows how to reach this server, so
it turns that path into a URL itself and fetches the bytes over the same road
it sends every other call down. Nothing new is exposed and no bytes travel
through the MCP call itself.

A `url` is only returned when MEDIA_PUBLIC_BASE_URL says this server is
reachable at some address directly. Leave it unset when the only way in is
through a gateway holding a secret: a link built from that secret would hand
the secret to whoever the link is given to.

The token in the path is the only thing protecting it, so it is long, random
and short lived, and it names nothing about the chat it came from.
"""

import os
import secrets
import time
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse

from telegram_mcp.runtime import *
# Underscored, so the wildcard above does not bring it in.
from telegram_mcp.runtime import _path_is_within_any_root

# token -> {"path": Path, "expires": epoch seconds, "content_type": str, "filename": str}
_STAGED: Dict[str, Dict[str, Any]] = {}

DEFAULT_TTL_MINUTES = 120
MAX_TTL_MINUTES = 24 * 60


def _staging_dir() -> Path:
    path = Path(os.getenv("MEDIA_STAGING_DIR", "/tmp/telegram-mcp-staged"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _public_base_url() -> Optional[str]:
    explicit = os.getenv("MEDIA_PUBLIC_BASE_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
    return f"https://{domain}" if domain else None


def _sweep(now: Optional[float] = None) -> int:
    """Throw away everything past its expiry. Cheap, and runs on every call."""
    now = time.time() if now is None else now
    dead = [token for token, item in _STAGED.items() if item["expires"] <= now]
    for token in dead:
        item = _STAGED.pop(token, None)
        if item:
            try:
                Path(item["path"]).unlink(missing_ok=True)
            except OSError:
                pass
    # A restart leaves files behind with no token pointing at them. Anything
    # older than the longest a token can live is unreachable, so it goes.
    try:
        cutoff = now - MAX_TTL_MINUTES * 60
        for leftover in _staging_dir().iterdir():
            if leftover.is_file() and leftover.stat().st_mtime < cutoff:
                leftover.unlink(missing_ok=True)
    except OSError:
        pass
    return len(dead)


@mcp.tool(
    annotations=ToolAnnotations(title="Stage Media", openWorldHint=True, destructiveHint=False)
)
@with_account(readonly=False)
@validate_id("chat_id")
async def stage_media(
    chat_id: Union[int, str],
    message_id: int,
    ttl_minutes: int = DEFAULT_TTL_MINUTES,
    ctx: Optional[Context] = None,
    account: str = None,
) -> str:
    """
    Download the media on a message and serve it back at a temporary path.

    For callers that are not on this machine and so cannot read the path
    download_media returns. Answers with `path` ("/staged/<token>"), which the
    caller joins to the address it already reaches this server on, and with
    `url` as well when MEDIA_PUBLIC_BASE_URL is set. It stops working when it
    expires.

    Args:
        chat_id: The chat ID or username.
        message_id: The message ID carrying the media.
        ttl_minutes: How long the path lives, up to 24 hours.
    """
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)
        msg = await cl.get_messages(entity, ids=message_id)
        if not msg or not msg.media:
            return "No media found in the specified message."

        _sweep()
        ttl = max(1, min(int(ttl_minutes or DEFAULT_TTL_MINUTES), MAX_TTL_MINUTES))
        token = secrets.token_hex(16)
        # Telethon picks the extension from the file itself when the path has
        # none, which is the only way to get the real type.
        stem = _staging_dir() / token
        downloaded = await cl.download_media(msg, file=str(stem))
        if not downloaded:
            return f"Download failed for message {message_id}."

        path = Path(downloaded).resolve()
        if not _path_is_within_any_root(path, [_staging_dir().resolve()]):
            path.unlink(missing_ok=True)
            return "Download failed: the file landed outside the staging directory."

        content_type = (
            getattr(getattr(msg, "document", None), "mime_type", None)
            or mimetypes.guess_type(path.name)[0]
            or "application/octet-stream"
        )
        _STAGED[token] = {
            "path": path,
            "expires": time.time() + ttl * 60,
            "content_type": content_type,
            "filename": path.name,
        }
        base = _public_base_url()
        answer = {
            "path": f"/staged/{token}",
            "filename": path.name,
            "content_type": content_type,
            "size": path.stat().st_size,
            "expires_at": datetime.fromtimestamp(
                _STAGED[token]["expires"], tz=timezone.utc
            ).isoformat(),
        }
        if base:
            answer["url"] = f"{base}/staged/{token}"
        return json.dumps(answer)
    except Exception as e:
        return log_and_format_error(
            "stage_media", e, chat_id=chat_id, message_id=message_id
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Unstage Media", openWorldHint=False, destructiveHint=True)
)
async def unstage_media(token: str, ctx: Optional[Context] = None) -> str:
    """
    Stop serving a staged file before it expires, and delete it.

    Args:
        token: The token from the stage_media URL.
    """
    item = _STAGED.pop(str(token).strip(), None)
    if not item:
        return "No staged file with that token."
    try:
        Path(item["path"]).unlink(missing_ok=True)
    except OSError:
        pass
    return "Staged file removed."


@mcp.custom_route("/staged/{token}", methods=["GET"])
async def serve_staged(request: Request):
    """Serve a staged file. The token is the only key, so it is all that is checked."""
    token = request.path_params.get("token", "")
    _sweep()
    item = _STAGED.get(token)
    if not item or not Path(item["path"]).exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(
        item["path"],
        media_type=item["content_type"],
        filename=item["filename"],
        headers={"cache-control": "private, max-age=60"},
    )


__all__ = ["stage_media", "unstage_media"]
