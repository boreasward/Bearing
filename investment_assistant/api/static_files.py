from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlparse

from investment_assistant.api.http import StaticResponse

STATIC_DIR = Path(__file__).resolve().parents[2] / "web" / "dist"


def static_response_for_path(path: str) -> StaticResponse | None:
    parsed_path = unquote(urlparse(path).path)
    if parsed_path == "/":
        target = STATIC_DIR / "index.html"
    elif parsed_path.startswith("/assets/"):
        target = STATIC_DIR / parsed_path.removeprefix("/")
    else:
        return None

    try:
        resolved = target.resolve()
        resolved.relative_to(STATIC_DIR.resolve())
    except Exception:
        return StaticResponse(403, "application/json; charset=utf-8", b'{"error":"forbidden"}')

    if not resolved.exists() or not resolved.is_file():
        if parsed_path == "/":
            body = json.dumps({"error": "frontend_not_built", "expected": str(STATIC_DIR / "index.html")}).encode("utf-8")
            return StaticResponse(503, "application/json; charset=utf-8", body)
        return None

    content_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
    if content_type.startswith("text/"):
        content_type += "; charset=utf-8"
    return StaticResponse(200, content_type, resolved.read_bytes())
