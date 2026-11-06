"""Importing the derivation chain must not pull in the database layer."""
import subprocess
import sys
from pathlib import Path


def test_import_is_database_free():
    code = ("import sys; import investment_assistant.derivation.chain as d; "
            "bad = [m for m in ('psycopg', 'investment_assistant.core.tx', "
            "'investment_assistant.derivation.views') if m in sys.modules]; "
            "assert not bad, bad; assert callable(d.derive_plan)")
    done = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                          cwd=Path(__file__).resolve().parent.parent)
    assert done.returncode == 0, done.stderr
