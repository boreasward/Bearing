"""New-member collection for the ``screen`` node (v2.8 D4; every fetch is a ``registry.run_adapter`` call, R23)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from investment_assistant.core import redact as families, tx as core_tx
from investment_assistant.data import collect


def backfill_new_members(tickers: list[str], *, session: date, now: datetime, deps: Any,
                         drop_after: datetime | None = None, fetched: set[str] | frozenset[str] = frozenset(),
                         ny_date: date | None = None, features: dict[str, bool] | None = None,
                         config: Any = None, trigger: str = 'scheduled', run_id: str | None = None,
                         bars_tally: 'collect.Tally | None' = None, news_tally: 'collect.Tally | None' = None,
                         sec_tally: 'collect.Tally | None' = None,
                         earnings_tally: 'collect.Tally | None' = None) -> dict[str, Any]:
    """News and SEC listings for the screen members the ``collect`` node did not cover (v2.8 D4), through the one
    ``registry.run_adapter`` path (``scope = 'screen_new_members'``, spec D15, R23): primary source, the backup when
    the run's frozen ``backup_sources`` is on, per-target tally, no second per-family fetch function.

    A member the range step did not fetch today (retained or locked, no longer in the range file) also gets its
    bars here, and every member its earnings date once more (the plan that follows reads the refreshed date).
    Nothing raises but ``Unavailable``: a ticker's failures come back in ``problems`` (``{ticker: ['bars: …',
    'news: …']}``), the earnings outcome in ``earnings`` (``{ticker: status or 'error: …'}``) and the listing's failure
    in ``filings_error``; with the tallies the outcomes are booked per target. ``drop_after`` (a resumed run's
    cutoff) only decides whether the dropped-after-cutoff counts are reported — every store already cuts at ``now``,
    the run's immutable cutoff. Returns ``{'bars', 'news', 'earnings', 'problems', 'filings', 'filings_error'}``
    (the first three per-ticker dicts, ``filings`` the listing summary or None)."""
    from investment_assistant.data import registry as data_registry
    from investment_assistant.data.adapter_support import adapter_deps_from_service
    from investment_assistant.data.sources.edgar import COMPANY_TICKERS_TARGET
    from investment_assistant.workflow.nodes.collect import _Family, sec_listing_summary

    out: dict[str, Any] = {'bars': {}, 'news': {}, 'earnings': {}, 'problems': {}, 'filings': None,
                           'filings_error': None}
    members = list(dict.fromkeys(tickers))
    projected = adapter_deps_from_service(deps, transaction=core_tx._tx, config=config, sleep=collect.FETCH_SLEEP)
    flags = features if features is not None else {}

    def run(adapter_id: str, targets: list[str], tally) -> _Family:
        family = _Family(adapter_id, 'daily')
        family.tally = tally if tally is not None else collect.Tally(adapter_id, 'daily')
        adapter = data_registry.get(adapter_id)
        try:
            with core_tx._tx() as conn:
                selected = adapter.targets(conn, 'screen_new_members', explicit=targets)
            data_registry.run_adapter(adapter, selected, scope='screen_new_members', trigger=trigger, run_id=run_id,
                                      session=session, now=now, deps=projected, features=flags, tally=family.tally,
                                      observe=family.observe,
                                      whole_call_target=COMPANY_TICKERS_TARGET if adapter_id == 'sec_filings' else None)
        except core_tx.Unavailable:
            raise
        except Exception as exc:    # noqa: BLE001 - the family could not run at all: every member's problem
            family.tally.record('*', exc)
            family.failure = families.sanitize_reason(exc)
        return family

    bars = run('bars', [t for t in members if t not in fetched], bars_tally)
    news = run('news', members, news_tally)
    earnings = run('earnings', members, earnings_tally)
    sec = run('sec_filings', members, sec_tally)

    for ticker in members:
        problems = out['problems'].setdefault(ticker, [])
        if ticker in fetched:
            out['bars'][ticker] = 'collected_in_screen'
        elif bars.ok(ticker):
            result = bars.result(ticker)
            out['bars'][ticker] = {'counts': {'inserted': result['inserted'], 'unchanged': result['existing']},
                                   'skipped': list(bars.facts(ticker).get('skipped', ()))}
        else:
            problems.append(f'bars: {bars.reason(ticker)}')
        if news.ok(ticker):
            result = news.result(ticker)
            out['news'][ticker] = {'inserted': result['inserted'], 'existing': result['existing'],
                                   **{k: news.facts(ticker)[k] for k in ('fetched', 'skipped') if k in news.facts(ticker)}}
            if drop_after is not None:
                out['news'][ticker]['after_cutoff_dropped'] = result.get('after_cutoff_dropped', 0)
        else:
            problems.append(f'news: {news.reason(ticker)}')
        # a failed fetch writes nothing and does not pose as "no earnings"
        out['earnings'][ticker] = (('unchanged' if earnings.result(ticker)['existing'] else 'inserted')
                                   if earnings.ok(ticker) else f'error: {earnings.reason(ticker)}')
    out['filings'], out['filings_error'] = sec_listing_summary(sec, drop_after=drop_after)
    return out
