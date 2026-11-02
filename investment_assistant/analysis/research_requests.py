"""The research second entrance (spec §6.3, D39 B, D43, D51): guarded lifecycle of company dossiers.

``create`` is the API's front door (validation, feature flag, per-day quota, idempotent replay;
a busy ``daily`` run only queues — D43 — an idle one submits the background run at once). ``run``
is one dossier attempt: state machine to ``running``, per-family collection through the registry,
the agent-4 dossier chain, one ``agent_runs(mode='research')`` row and one ``research_versions``
row, all failure classes mapped to error versions with the request back to ``queued`` until the
third attempt closes it. Dossiers never open chains, write plans or trade (D39 B)."""
from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

from investment_assistant.analysis import repo as analysis_repo
from investment_assistant.analysis.agent import runner as agent_runner
from investment_assistant.core import features as core_features
from investment_assistant.core import runner as core_runner
from investment_assistant.core import schema as core_schema
from investment_assistant.core import tx as core_tx
from investment_assistant.core.constants import NY, RESEARCH_DAILY_MAX
from investment_assistant.data.adapters import bars as bars_module
from investment_assistant.data.identity import stock_id

QUESTION_MAX = 500
HORIZON_RANGE = (1, 365)
COLLECT_FAMILIES = ('bars', 'news', 'sec_filings', 'earnings', 'fundamentals', 'sec_text', 'sector')
MAX_ATTEMPTS = 3


class FeatureDisabled(RuntimeError):
    """The company_researcher flag is off (HTTP 409 at the API)."""


class ResearchQuota(RuntimeError):
    """RESEARCH_DAILY_MAX dossier requests were already created today (HTTP 409 at the API)."""


def _ready(conn) -> bool:
    return core_schema.five_layers_ready(conn)


def create(*, ticker: str, question: str | None = None, horizon_days: int | None = None,
          request_id, now: datetime) -> dict:
    """Validate and queue one dossier request; identical ``request_id`` replays the stored row (D51)."""
    try:
        stock_id(ticker.strip() if isinstance(ticker, str) else '')
    except (ValueError, AttributeError):
        raise ValueError('invalid_ticker') from None
    try:
        request_id = str(uuid.UUID(str(request_id)))
    except ValueError:
        raise ValueError('invalid_request_id') from None
    if question is not None and (not isinstance(question, str) or not question.strip()
                                 or len(question) > QUESTION_MAX):
        raise ValueError('invalid_question')
    if horizon_days is not None and (isinstance(horizon_days, bool) or not isinstance(horizon_days, int)
                                     or not HORIZON_RANGE[0] <= horizon_days <= HORIZON_RANGE[1]):
        raise ValueError('invalid_horizon_days')
    with core_tx._tx() as conn:
        if not _ready(conn):
            raise core_tx.Unavailable('migration_031_missing')
        if not core_features.frozen(conn)['company_researcher']:
            raise FeatureDisabled('company_researcher')
        existing = _one_by_request_id(conn, request_id)
        if existing is not None:      # D51: a replayed request_id never re-runs anything
            return {'id': existing['id'], 'status': existing['status'], 'ticker': existing['ticker'],
                    'request_id': str(existing['request_id'])}
        created_today = conn.execute(
            "SELECT count(*) FROM research_requests "
            "WHERE (created_at AT TIME ZONE 'America/New_York')::date = %s",
            (now.astimezone(NY).date(),)).fetchone()
        assert created_today is not None               # count(*) always answers one row
        if created_today[0] >= RESEARCH_DAILY_MAX:
            raise ResearchQuota(f'{RESEARCH_DAILY_MAX} 份/天')
        row = analysis_repo.insert_research_request(conn, {
            'id': str(uuid.uuid4()), 'ticker': ticker.strip(), 'question': question,
            'horizon_days': horizon_days, 'source': 'manual', 'request_id': request_id,
            'created_at': now})
    if not _daily_running():
        _submit_background(row['id'])
    return {'id': row['id'], 'status': row['status'], 'ticker': row['ticker'],
            'request_id': str(row['request_id'])}


def _one_by_request_id(conn, request_id):
    from investment_assistant.core.dbutil import _one

    return _one(conn, 'SELECT id, ticker, status, request_id FROM research_requests WHERE request_id = %s',
                (request_id,))


def _daily_running() -> bool:
    from investment_assistant.core import locks as core_locks

    try:
        return core_locks.is_running()
    except Exception:      # noqa: BLE001 — no database to ask: queue now, the drain decides later
        return False


def _submit_background(request_id: str) -> str:
    def job() -> dict[str, Any]:
        return run(request_id, now=datetime.now(UTC), deps=core_tx.Deps())

    return core_runner.submit('research', job)


def close(*, request_id: str, reason: str, now: datetime) -> dict:
    """Manually end a dossier (queued / waiting_data / running → closed)."""
    with core_tx._tx() as conn:
        row = analysis_repo.research_request(conn, request_id)
        if row is None:
            raise LookupError(f'没有这份研究请求：{request_id}')
        if row['status'] == 'closed':
            return row
        if not analysis_repo.update_research_status(conn, row['id'], from_status=row['status'],
                                                    to_status='closed', closed_reason=reason[:300]):
            raise LookupError(f'研究请求状态已变化：{request_id}')
        closed = analysis_repo.research_request(conn, request_id)
        assert closed is not None
        return closed


VERSION_KEYS = ('version_no', 'status', 'action', 'summary', 'cutoff_at', 'agent_run_id', 'output', 'review_when',
                'tokens', 'elapsed_ms', 'created_at')


def _headline(row: dict[str, Any], versions: list[dict[str, Any]]) -> dict[str, Any]:
    """The request row with its list-card fields: the newest version's one-liner and cutoff, and the tokens
    all attempts together cost (§12.1)."""
    latest = versions[-1] if versions else None
    return {**row, 'latest_summary': latest['summary'] if latest else None,
            'cutoff_at': latest['cutoff_at'] if latest else None,
            'tokens': sum(v['tokens'] or 0 for v in versions)}


def list_requests(*, status: str | None = None, now: datetime | None = None) -> list[dict[str, Any]]:
    """Newest first, optionally filtered by one status (§12.1 GET /api/research)."""
    with core_tx.read_only_tx() as conn:
        if not _ready(conn):
            raise core_tx.Unavailable('migration_031_missing')
        return [_headline(row, analysis_repo.research_versions(conn, row['id']))
                for row in analysis_repo.list_research_requests(conn, status=status)]


def detail(request_id: str, *, now: datetime | None = None) -> dict[str, Any] | None:
    """``{request, versions, chain}``; the chain is None — a dossier never opens one (D39 B). A version
    carries exactly the §12.1 keys; the request carries its list-card fields too."""
    with core_tx.read_only_tx() as conn:
        if not _ready(conn):
            raise core_tx.Unavailable('migration_031_missing')
        row = analysis_repo.research_request(conn, request_id)
        if row is None:
            return None
        versions = analysis_repo.research_versions(conn, request_id)
        return {'request': _headline(row, versions),
                'versions': [{key: version[key] for key in VERSION_KEYS} for version in versions],
                'chain': None}


def latest_dossier(conn, ticker: str, *, cutoff_at: datetime) -> dict[str, Any] | None:
    """The newest actual conclusion known by the cutoff — what ``get_research_dossier`` reads."""
    return analysis_repo.latest_dossier_for(conn, ticker, cutoff_at=cutoff_at)


def run(request_id: str, *, now: datetime, deps) -> dict[str, Any]:
    """One dossier attempt (§6.3 run). Raises LookupError for an unknown or non-eligible request."""
    started = time.monotonic()
    with core_tx._tx() as conn:
        row = analysis_repo.research_request(conn, request_id)
        if row is None or row['status'] not in ('queued', 'waiting_data'):
            raise LookupError(f'没有待处理的研究请求：{request_id}')
        if not analysis_repo.update_research_status(conn, row['id'], from_status=row['status'],
                                                    to_status='running'):
            raise LookupError(f'研究请求状态已变化：{request_id}')
    collection = _collect(row['ticker'], now=now, deps=deps)
    out = agent_runner.run_research(row, now=now, deps=deps, collection=collection,
                                    started=started)
    out['collected'] = collection
    return out


def _collect(ticker: str, *, now: datetime, deps) -> dict[str, int]:
    """§6.3 ②: the seven per-ticker families through the registry, scope 'research'; a family
    failure is recorded (its data_fetch_runs row says so) and never aborts the dossier — the
    researcher writes the gap into ``blocked``."""
    from investment_assistant.data import collect as data_collect
    from investment_assistant.data import registry as data_registry
    from investment_assistant.data.adapter_support import adapter_deps_from_service

    session = bars_module.latest_closed_session(now)
    projected = adapter_deps_from_service(deps, transaction=core_tx._tx, sleep=data_collect.FETCH_SLEEP)
    with core_tx._tx() as conn:           # R20: the current runtime switches (no row = on), like process_data_requests
        features = core_features.frozen(conn)
    counts = {}
    for family in COLLECT_FAMILIES:
        adapter = data_registry.get(family)
        lock = (data_collect.family_locks([family]) if adapter.lock else _null_lock())
        with lock:
            tally = data_registry.run_adapter(adapter, [ticker], scope='research', trigger='manual_research',
                                              run_id=None, session=session, now=now, deps=projected,
                                              features=features)
        fetch_row = tally.row(trigger='manual_research', run_id=None, session=session,
                              started_at=now, finished_at=now, scope='ticker', ticker=ticker)
        data_collect.write_runs_with_ids([fetch_row])
        target = next((item for item in (fetch_row.get('detail') or {}).get('targets') or []
                       if item.get('target') == ticker), {})
        counts[family] = target.get('rows') or 0
    return counts


class _NullLock:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _null_lock():
    return _NullLock()


_NY = NY


# ---------------------------------------------------------------- command line (spec §12.3)

def main(argv=None) -> int:
    """``run <request_id>`` runs one queued dossier attempt in the foreground (the way the background job does);
    ``show <request_id>`` prints the request with its versions. Exit 2 for an unknown request, an unready schema or
    bad arguments."""
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(prog='python -m investment_assistant.analysis.research_requests',
                                     description='研究请求：前台跑一次、查看详情。')
    commands = parser.add_subparsers(dest='command', required=True)
    for name, text in (('run', '前台跑一次排队中或等数据的研究请求'), ('show', '查看研究请求与各版结论')):
        commands.add_parser(name, help=text).add_argument('request_id')
    args = parser.parse_args(argv)
    from dotenv import load_dotenv

    load_dotenv()
    now = datetime.now(UTC)
    try:
        if args.command == 'run':
            found = run(args.request_id, now=now, deps=core_tx.Deps())
        else:
            found = detail(args.request_id, now=now)
            if found is None:
                raise LookupError(f'没有这个研究请求：{args.request_id}')
        print(json.dumps(found, ensure_ascii=False, default=str, indent=2))
        return 0
    except (LookupError, ValueError, core_tx.Unavailable) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
