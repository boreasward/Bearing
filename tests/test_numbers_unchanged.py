"""The frozen numeric snapshot of every calculation convention (v2.12 Task 0, Step 3).

``tests/fixtures/numbers_snapshot.json`` (generated once by
``scripts/make_numbers_snapshot.py``) pins the exact numbers of rules v1, screen-1,
regime-2, the plan math, derivation, paper-fill settlement and the monthly /
discipline / tracks statistics before the five-layer move. Re-evaluating the shared
case set must reproduce it key for key: any drift during the refactor is a failure
here, not a silent change of convention. See the v2.12–v2.15 plan Global Constraints
「不改口径」."""
import json
from pathlib import Path

from _numbers_cases import cases

SNAPSHOT = Path(__file__).resolve().parent / 'fixtures' / 'numbers_snapshot.json'


def _stored() -> dict:
    return json.loads(SNAPSHOT.read_text(encoding='utf-8'))


def test_every_snapshot_key_matches():
    live = cases()
    stored = _stored()
    for key in sorted(stored):
        assert key in live, f'{key} missing from the live case set'
        assert live[key] == stored[key], f'{key} changed: {live[key]!r} != {stored[key]!r}'


def test_snapshot_has_no_missing_cases():
    assert set(cases()) == set(_stored())
