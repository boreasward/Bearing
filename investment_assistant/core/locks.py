"""Data-family lock keys (the session advisory locks of ``data/collect``)."""
from __future__ import annotations


def family_lock_key(family_id: str) -> str:
    """The session-lock key of a data family."""
    return f'data_family:{family_id}'
