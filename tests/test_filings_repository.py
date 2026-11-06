from datetime import date

from investment_assistant import db


class FakeCursor:
    def __init__(self, store):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.store.append((sql, params))

    def fetchall(self):
        return []

    def fetchone(self):
        return None


class FakeConn:
    def __init__(self):
        self.store = []
        self.commits = 0

    def cursor(self):
        return FakeCursor(self.store)

    def commit(self):
        self.commits += 1


def test_upsert_filing_conflicts_on_accession():
    conn = FakeConn()
    db.upsert_filing(
        conn, ticker="AAPL", form="10-Q", filing_date=date(2026, 6, 28),
        accession_number="acc-1", primary_document="q.htm",
        file_path="/data/AAPL/10-Q/acc-1-q.htm",
    )
    sql, params = conn.store[0]
    assert "INSERT INTO filings" in sql
    assert "ON CONFLICT (accession_number, primary_document)" in sql
    assert params["ticker"] == "AAPL" and params["run_id"] is None
    assert conn.commits == 1


def test_upsert_filing_records_acceptance_without_overwriting_known_values():
    conn = FakeConn()
    db.upsert_filing(
        conn, ticker="NVDA", form="8-K", filing_date=date(2026, 8, 27),
        accession_number="acc-2", primary_document="k.htm", file_path="/x",
        accepted_at="2026-08-27T16:20:31-04:00", items=["2.02"],
    )
    sql, params = conn.store[0]
    assert "accepted_at" in sql and "items" in sql
    assert "COALESCE(filings.accepted_at, EXCLUDED.accepted_at)" in sql
    assert params["accepted_at"].isoformat() == "2026-08-27T16:20:31-04:00"
    assert params["items"] == ["2.02"]


def test_upsert_filing_defaults_acceptance_to_unknown():
    conn = FakeConn()
    db.upsert_filing(conn, ticker="AAPL", form="10-Q", filing_date=date(2026, 6, 28),
                     accession_number="acc-3", primary_document="q.htm", file_path="/x")
    _, params = conn.store[0]
    assert params["accepted_at"] is None and params["items"] is None
