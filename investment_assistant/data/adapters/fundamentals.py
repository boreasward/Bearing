"""SEC companyfacts: reuse source normalization and economic-revision storage."""
from investment_assistant.data import repo
from investment_assistant.data.adapter_types import Source
from investment_assistant.data.adapter_support import ONCE, make_adapter, sec_outcome, sec_source
from investment_assistant.data.identity import stock_id
from investment_assistant.data.research_rows import financial_rows
from investment_assistant.data.sources.fundamentals import PolicyUSSources


def _fetch(target, *, session, start, end, deps, now):
    source = sec_source(deps)
    def fetch():
        payload = PolicyUSSources(source).companyfacts(target)
        rows, _ = financial_rows(payload, root=source.evidence_dir)
        return rows
    return sec_outcome(source, fetch)


def _store(conn, rows, *, target, source_id, now):
    return repo.store_observation_rows(conn, stock_id(target), 'financials', rows, now=now)


def _latest(conn, targets, *, cutoff_at):
    return repo.latest_observation_dates(conn, targets, dataset='financials', cutoff_at=cutoff_at)


ADAPTER = make_adapter('fundamentals', '公司财务', 'SEC标准公司财务事实', 'us_research_observations(financials)',
    (Source('sec', _fetch, ONCE, 'companyfacts复用既有归一化；EDGAR fetch-1与字节证据'),),
    _store, _latest, days=120, coverage_note='覆盖 = 最新disclosed_at不早于session前120天。')
