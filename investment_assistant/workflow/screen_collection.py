"""Range and new-member collection for daily workflow nodes."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Callable

from investment_assistant.core import dbutil as core_dbutil, schema as core_schema, tx as core_tx
from investment_assistant.core.constants import BENCHMARK, NY
from investment_assistant.data import repo as data_repo
from investment_assistant.data.adapters import bars
from investment_assistant.analysis import repo as analysis_repo, synthesis, pool as pool_module
from investment_assistant.execution import repo as execution_repo, repo_legacy as execution_repo_legacy
from investment_assistant.execution import chains as execution_chains, plan_context as execution_plan_context
from investment_assistant.execution import ledger, timeline

from investment_assistant.data import collect
from investment_assistant.data.adapters import earnings as earnings_module
from investment_assistant.core.constants import HISTORY_DAYS

COLLECT_LOOKBACK_DAYS = 400

def collect_range(conn_factory, *, session: date, now: datetime, targets: list[str],
                  deps: Any, tally: 'collect.Tally | None' = None) -> tuple[set[str], list[str], dict[str, str]]:
    """Collect daily bars and earnings for range tickers outside the manual pool (the ``collect`` node's range step).

    Bar failures are skips, never filters: they surface in the batch-level
    summary['sources']['bars'] and never count as illiquid or no-setup. Earnings
    failures leave the date unknown, which screen-1 only notes. With ``tally`` each ticker's bars outcome is
    booked there (target = ticker, attempts and all)."""
    fetched: set[str] = set()
    failures: list[str] = []
    reasons: dict[str, str] = {}
    for ticker in targets:
        try:
            collect.bars_one(ticker, session=session, now=now, deps=deps,
                             lookback_days=COLLECT_LOOKBACK_DAYS, tally=tally)
            fetched.add(ticker)
        except Exception as exc:
            failures.append(ticker)
            reasons[ticker] = str(exc)
            continue
        try:
            row = earnings_module.fetch_next(ticker, fetcher=deps.earnings_fetcher,
                                             today=now.astimezone(NY).date())
            with conn_factory() as conn:
                data_repo.store_earnings(conn, ticker, row, now=now)
        except Exception:
            pass  # the next earnings date stays unknown; screen-1 notes it, never excludes
    return fetched, failures, reasons


def backfill_new_members(tickers: list[str], *, session: date, now: datetime, deps: Any,
                         drop_after: datetime | None = None, fetched: set[str] | frozenset[str] = frozenset(),
                         ny_date: date | None = None,
                         bars_tally: 'collect.Tally | None' = None, news_tally: 'collect.Tally | None' = None,
                         sec_tally: 'collect.Tally | None' = None) -> dict[str, Any]:
    """News and SEC listings for the screen members the ``collect`` node did not cover (v2.8 D4).

    A member the range step did not fetch today (retained or locked, no longer in the range file) also gets its
    bars here, and every member its earnings date once more (as the v2.7 per-ticker loop gave them: the plan that
    follows reads the refreshed date). Nothing raises but ``Unavailable``: a ticker's failures come back in
    ``problems`` (``{ticker: ['bars: …', 'news: …']}``), the earnings outcome in ``earnings`` (``{ticker: status or
    'error: …'}``) and the listing's failure in ``filings_error``; with the tallies the outcomes are booked per
    target. Returns ``{'bars', 'news', 'earnings', 'problems', 'filings', 'filings_error'}`` (the first three are
    per-ticker dicts, ``filings`` the ``collect.sec_listing`` result or None)."""

    out: dict[str, Any] = {'bars': {}, 'news': {}, 'earnings': {}, 'problems': {}, 'filings': None,
                           'filings_error': None}
    today = ny_date or now.astimezone(NY).date()
    for ticker in tickers:
        problems = out['problems'].setdefault(ticker, [])
        if ticker in fetched:
            out['bars'][ticker] = 'collected_in_screen'
        else:
            try:
                out['bars'][ticker] = collect.bars_one(ticker, session=session, now=now, deps=deps,
                                                       lookback_days=HISTORY_DAYS, tally=bars_tally)
            except core_tx.Unavailable:
                raise
            except Exception as exc:
                problems.append(f'bars: {exc}')
        try:
            out['news'][ticker] = collect.news_one(ticker, now=now, deps=deps, drop_after=drop_after,
                                                   tally=news_tally)
        except core_tx.Unavailable:
            raise
        except Exception as exc:
            problems.append(f'news: {exc}')
        try:
            row = earnings_module.fetch_next(ticker, fetcher=deps.earnings_fetcher, today=today)
            with core_tx._tx() as conn:
                out['earnings'][ticker] = data_repo.store_earnings(conn, ticker, row, now=now)['status']
        except core_tx.Unavailable:
            raise
        except Exception as exc:     # a failed fetch writes nothing and does not pose as "no earnings"
            out['earnings'][ticker] = f'error: {exc}'
    try:
        out['filings'] = collect.sec_listing(tickers, session=session, now=now, deps=deps, tally=sec_tally,
                                             drop_after=drop_after)
    except core_tx.Unavailable:
        raise
    except Exception as exc:
        out['filings_error'] = str(exc)
        if sec_tally is not None:
            sec_tally.fail('*', exc)
    return out
