"""The command-line entries ``data.registry list / fetch`` and ``derivation.indicators list / compute``.
Disposable schema, synthetic bars, no network."""

import json
import subprocess
import sys
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

import investment_assistant.core.tx as core_tx
from investment_assistant.data import collect, registry
from investment_assistant.data.calendar import add_trading_days
from investment_assistant.derivation.indicators import __main__ as indicators_cli
from investment_assistant.derivation.indicators import registry as indicator_registry

ROOT = Path(__file__).resolve().parent.parent
SESSION = date(2026, 10, 22)  # Thursday
NOW = datetime(2026, 10, 22, 22, 30, tzinfo=UTC)  # 18:30 New York
MODULES = (
    "investment_assistant.data.registry",
    "investment_assistant.derivation.indicators",
    "investment_assistant.daily",
)


def frame(ticker, end):
    closes = (
        [500.0] * 300
        if ticker == "SPY"
        else [50 + i * 0.25 for i in range(296)] + [50 + 296 * 0.25 - 6] * 3 + [50 + 296 * 0.25 + 1]
    )
    days = [add_trading_days(SESSION, -(len(closes) - 1 - i)) for i in range(len(closes))]
    rows = [(d, c) for d, c in zip(days, closes) if d <= end]
    index = pd.DatetimeIndex([pd.Timestamp(d, tz="America/New_York") for d, _ in rows])
    return pd.DataFrame(
        {
            "Open": [c for _, c in rows],
            "High": [c * 1.02 for _, c in rows],
            "Low": [c * 0.99 for _, c in rows],
            "Close": [c for _, c in rows],
            "Adj Close": [c for _, c in rows],
            "Volume": [1000] * len(rows),
            "Stock Splits": [0.0] * len(rows),
        },
        index=index,
    )


class Sources:
    def __init__(self):
        self.calls = []

    def bars(self, ticker, start, end_exclusive):
        self.calls.append(("bars", ticker))
        return frame(ticker, end_exclusive - timedelta(days=1))

    def news(self, ticker):
        self.calls.append(("news", ticker))
        return [
            {
                "content": {
                    "title": f"{ticker} headline",
                    "pubDate": "2026-10-22T12:00:00Z",
                    "canonicalUrl": {"url": f"https://news.example.com/{ticker}"},
                }
            }
        ]

    def discover(self, tickers, start, end):
        self.calls.append(("sec", tuple(sorted(tickers))))
        return []


@pytest.fixture
def db(workbench_db, monkeypatch):
    @contextmanager
    def connection():
        yield workbench_db

    monkeypatch.setattr(core_tx, "connection", connection)
    return workbench_db


def deps(sources, **overrides):
    return core_tx.Deps(bar_fetcher=sources.bars, news_fetcher=sources.news, filing_source=sources, **overrides)


@pytest.mark.parametrize("module", MODULES)
def test_module_entry_prints_help_without_a_database(module):
    done = subprocess.run(
        [sys.executable, "-m", module, "--help"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={"PATH": "", "PYTHON_DOTENV_DISABLED": "1"},
    )
    assert done.returncode == 0 and "usage" in done.stdout and module in done.stdout, done.stderr


def test_no_package_level_second_entry():
    assert not (ROOT / "investment_assistant" / "data" / "__main__.py").exists()


# ---------------------------------------------------------------- data.registry


def test_registry_list_has_every_family(capsys):
    assert registry.main(["list"]) == 0
    out = capsys.readouterr().out
    assert [line.split()[0] for line in out.splitlines()[1:]] == list(registry.ids())


def test_registry_fetch_argument_errors(db, capsys):
    assert registry.main(["fetch", "nope"]) == 2  # unknown family
    assert registry.main(["fetch", "bars"]) == 2  # a per-ticker family needs --ticker
    assert registry.main(["fetch", "fred", "--ticker", "NVDA"]) == 2  # a whole-family one takes none
    assert registry.main(["fetch", "bars", "--ticker", "not a ticker"]) == 2
    err = capsys.readouterr().err
    assert "未知数据族" in err and "--ticker" in err and "非法 ticker" in err
    assert db.execute("SELECT count(*) FROM data_fetch_runs").fetchone()[0] == 0


def test_registry_fetch_one_ticker_writes_a_manual_family_row(db, monkeypatch, capsys):
    fake = deps(Sources())
    monkeypatch.setattr(core_tx, "Deps", lambda: fake)
    monkeypatch.setattr(collect, "wall_clock", lambda: NOW)
    assert registry.main(["fetch", "bars", "--ticker", "mu"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == "success" and printed["ticker"] == "MU" and printed["ok"] == 1
    assert db.execute("SELECT count(*) FROM price_bars WHERE ticker = 'MU'").fetchone()[0] > 0
    assert db.execute(
        "SELECT family, job, trigger, scope, ticker, session, status FROM data_fetch_runs"
    ).fetchall() == [("bars", "workbench", "manual_family", "ticker", "MU", SESSION, "success")]


# ---------------------------------------------------------------- derivation.indicators


def test_indicators_list_has_every_indicator(capsys):
    assert indicators_cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert [line.split()[0] for line in out.splitlines()[1:]] == [i.id for i in indicator_registry.INDICATORS]


def test_indicators_compute_refuses_what_it_cannot_do_honestly(db, capsys):
    for args in (["breadth", "NVDA"], ["sector_strength", "NVDA"], ["nope", "NVDA"], ["atr14", "not a ticker"]):
        assert indicators_cli.main(["compute", *args]) == 2
    err = capsys.readouterr().err
    assert "indicator_requires_universe" in err and "indicator_requires_sector_etf" in err and "未知指标" in err
    found = indicators_cli.compute_one("atr14", "NVDA", now=NOW)  # no stored bars: insufficient history
    assert found["value"] is None and found["computed_through"] is None
