from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StaticResponse:
    status: int
    content_type: str
    body: bytes
