"""One-time generator of tests/fixtures/numbers_snapshot.json (v2.12 Task 0, Step 2).

Freezes the numeric output of every calculation convention (rules v1, screen-1, regime-2,
plan math, derivation, paper-fill settlement, monthly / discipline / tracks) before the
five-layer move, so any behavioural drift during the refactor shows as a test failure
(``tests/test_numbers_unchanged.py``) instead of a silent change. The case set itself
lives in ``tests/_numbers_cases.py`` and is shared with that test.

Run from the repository root:  .venv/bin/python scripts/make_numbers_snapshot.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from _numbers_cases import cases  # noqa: E402


def main() -> None:
    values = cases()
    target = ROOT / 'tests' / 'fixtures' / 'numbers_snapshot.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(values, indent=2, sort_keys=True, ensure_ascii=False) + '\n',
                      encoding='utf-8')
    print(f'{target.relative_to(ROOT)}: {len(values)} keys')


if __name__ == '__main__':
    main()
