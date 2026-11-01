"""Source-backed company guidance, immutable revisions and exact-basis comparisons."""
from __future__ import annotations

import math
from datetime import UTC, date, datetime
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from investment_assistant.data import repo
from investment_assistant.data.fundamentals import StrictModel, stock_id, valid_source_link
import re
from investment_assistant.core import tx as core_tx
from investment_assistant.derivation.indicators.fiscal import financial_series, performance_view, valuation_view
from investment_assistant.analysis.fundamental_templates import research_template
from investment_assistant.data.adapters import bars as price_bars

DATASETS = ('financials', 'market', 'filings')


class Guidance(StrictModel):
    metric: str = Field(min_length=1, max_length=200)
    target_start: date
    target_end: date
    basis: str = Field(min_length=1, max_length=300)
    unit: str = Field(min_length=1, max_length=100)
    low: float
    high: float
    issued_at: datetime
    source_url: str = Field(min_length=1, max_length=2000)
    source_locator: str = Field(min_length=1, max_length=2000)
    note: str = Field(default='', max_length=5000)
    supersedes_id: int | None = Field(default=None, ge=1)

    _valid_link = field_validator('source_url')(valid_source_link)

    @field_validator('low', 'high', mode='before')
    @classmethod
    def numeric_amount(cls, value):
        if isinstance(value, bool):
            raise ValueError('指引金额须为有限数值')
        return value

    @model_validator(mode='after')
    def coherent(self):
        if self.target_start > self.target_end or self.low > self.high or self.issued_at.date() > self.target_end:
            raise ValueError('指引期间或上下限不连贯')
        if self.issued_at.tzinfo is None:
            raise ValueError('发布时间须含时区')
        if self.issued_at > datetime.now(UTC):
            raise ValueError('发布时间不可在未来')
        # Use explicit normalized bases; the word GAAP alone loses attribution.
        if ':' not in self.basis or self.basis.split(':', 1)[0] not in {'us-gaap', 'non-gaap', 'company'}:
            raise ValueError('须指定完整会计口径，例如us-gaap:consolidated')
        if not self.metric.strip() or not self.unit.strip() or not self.source_locator.strip():
            raise ValueError('指标、单位与原文定位不可空白')
        return self


def append(ticker, payload):
    sid = stock_id(ticker)
    model = Guidance.model_validate(payload)
    data = model.model_dump(mode='json')
    with connection() as conn:
        natural_key = 'manual:' + str(uuid4())
        if model.supersedes_id is not None:
            with conn.cursor() as cur:
                cur.execute('''SELECT natural_key,payload FROM us_research_observations
                    WHERE id=%s AND security_id=%s AND dataset='guidance' ''', (model.supersedes_id, sid))
                original = cur.fetchone()
            if not original:
                raise ValueError('被修订指引不属于当前证券的指引记录')
            prior = original[1]
            if any(data[k] != prior.get(k) for k in ('metric', 'target_start', 'target_end')):
                raise ValueError('修订须属于同一指标与目标期间')
            if prior.get('issued_at') and model.issued_at < datetime.fromisoformat(prior['issued_at'].replace('Z', '+00:00')):
                raise ValueError('修订发布时间不可早于原指引')
            natural_key = original[0]
        result = repo.store_rows(conn, sid, 'guidance', [{'natural_key': natural_key, 'payload': data,
            'disclosed_at': model.issued_at, 'source_url': model.source_url}])[0]
        conn.commit()
    return result


def list_records(ticker, *, as_of=None):
    with connection() as conn:
        return repo.read_history(conn, stock_id(ticker), 'guidance', as_of=_cutoff(as_of))


def _eligible(record, cutoff):
    if cutoff is None:
        return True
    for field in ('first_seen_at', 'disclosed_at', 'issued_at', 'filed'):
        value = record.get(field)
        if value:
            try:
                parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
                if field == 'filed' and len(str(value)) == 10:
                    parsed = parsed.replace(tzinfo=UTC)
                if parsed.tzinfo is None or parsed > cutoff:
                    return False
            except (TypeError, ValueError):
                return False
    return True


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def comparisons(records, financials, *, as_of=None):
    """Never combine accounting/share bases or periods to manufacture a beat."""
    cutoff = _cutoff(as_of)
    results = []
    for record in records:
        if not _eligible(record, cutoff):
            continue
        low, high = record.get('low'), record.get('high')
        result = {'guidance_id': record.get('id'), 'metric': record.get('metric'),
            'target_start': record.get('target_start'), 'target_end': record.get('target_end'),
            'basis': record.get('basis'), 'unit': record.get('unit'), 'actual': None,
            'low': low, 'high': high, 'midpoint': (low / 2 + high / 2) if _finite(low) and _finite(high) else None,
            'status': 'not_comparable', 'input_ids': [], 'reason': '缺少同指标、期间、单位及已核实口径的实际结果'}
        basis = record.get('basis', '')
        # Identical unknown share-basis strings still do not prove split comparability.
        verified = basis in {'us-gaap:consolidated', 'us-gaap:parent', 'us-gaap:common'}
        candidates = []
        if verified and _finite(low) and _finite(high) and low <= high:
            for period in financials:
                if (period.get('start'), period.get('end')) != (str(record.get('target_start')), str(record.get('target_end'))):
                    continue
                actual = period.get('metrics', {}).get(record.get('metric'), {})
                if (actual.get('unit'), actual.get('basis')) != (record.get('unit'), basis):
                    continue
                if not _finite(actual.get('value')) or not _eligible(actual, cutoff):
                    continue
                if not all(_eligible(source, cutoff) for source in actual.get('sources', [])):
                    continue
                candidates.append(actual)
        if candidates and len({r['value'] for r in candidates}) == 1:
            actual = candidates[0]
            value = actual['value']
            result.update(actual=value, status='below_range' if value < low else 'above_range' if value > high else 'in_range',
                input_ids=list(dict.fromkeys(id_ for c in candidates for id_ in c.get('input_ids', []))), reason=None)
        results.append(result)
    return results


def _connection():
    """core.tx's connection with this subsystem's legacy error contract: an unconfigured
    database stays a ``ValueError`` (HTTP 400), not the workbench's ``Unavailable``.
    Guidance callers use the local ``connection`` alias below."""
    try:
        return core_tx.connection()
    except core_tx.Unavailable:
        raise ValueError('未配置数据库，尚未采集美股研究数据') from None


connection = _connection   # guidance entry points share this error-contract adapter


def _financial_observations(records):
    observations = []
    for record in records:
        if record.get('conflicts'):
            for candidate in record['conflicts']:
                observations.append({**candidate, 'id': record['id'], 'security_id': record['security_id'],
                    'first_seen_at': record['first_seen_at'], 'disclosed_at': record['disclosed_at']})
        else:
            observations.append(record)
    return observations


def _cutoff(as_of):
    try:
        cutoff = datetime.fromisoformat(as_of) if isinstance(as_of, str) else as_of
        if cutoff is not None and (not isinstance(cutoff, datetime) or cutoff.tzinfo is None):
            raise ValueError()
        return cutoff
    except (ValueError, TypeError):
        raise ValueError('历史观察截止时间须为含时区的ISO时间') from None


def market_from_bars(rows, benchmarks):
    """Project stored price facts into the existing market response, without source I/O."""
    def project(items):
        return [{'id': row['id'], 'date': row['price_date'].isoformat(),
                 **{key: row[key] for key in ('open', 'high', 'low', 'close', 'volume')},
                 'adjusted_close': row['adj_close'], 'currency': None,
                 'source_url': None, 'source_hash': None}
                for row in items]

    bars = project(rows)
    compared = {symbol: project(items) for symbol, items in benchmarks.items()}
    last = bars[-1] if bars else None
    quote = ({'date': last['date'], 'currency': None, 'price': last['close'],
              'price_basis': 'stored_daily_close', 'shares': None, 'market_cap': None,
              'market_cap_verified': False, 'share_class_basis': 'unverified',
              'source_url': None, 'source_hash': None} if last else {})
    return {'bars': bars, 'benchmarks': compared, 'quote': quote,
            'performance': performance_view(bars, compared),
            'limitations': ['价格来自已入库日线；未接入可核验市值与股本口径'],
            'limitations_scope': 'current_collection_state'}


def read_market(conn, ticker, *, as_of=None):
    """Shared read port for fundamentals and the upper instrument view (D48)."""
    now = _cutoff(as_of) or datetime.now(UTC)
    session = price_bars.latest_closed_session(now)
    rows = {symbol: repo.read_bars(conn, symbol, session, cutoff_at=now, limit=2000)
            for symbol in dict.fromkeys((ticker, 'SPY', 'QQQ'))}
    return market_from_bars(rows[ticker], {symbol: rows[symbol] for symbol in ('SPY', 'QQQ')})


def view(ticker, *, as_of=None):
    sid = stock_id(ticker)
    ticker = sid.rsplit(':', 1)[-1]
    cutoff = _cutoff(as_of)
    with _connection() as conn:
        rows = {ds: repo.read_rows(conn, sid, ds, as_of=cutoff) for ds in DATASETS if ds != 'market'}
        states = repo.states(conn, sid)
        guidance_records = repo.read_history(conn, sid, 'guidance', as_of=cutoff)
        market = read_market(conn, ticker, as_of=cutoff)
        rows['market'] = [*market['bars'], *market['benchmarks']['SPY'], *market['benchmarks']['QQQ']]
        states['market'] = {'dataset': 'market', 'status': 'ready' if market['bars'] else 'empty',
                            'row_count': len(rows['market']), 'attempted_at': None, 'succeeded_at': None,
                            'error': None, 'details': {}, 'last_success_details': {}}
    statuses = []
    for ds in DATASETS:
        state = states.get(ds, {'dataset': ds, 'status': 'never_collected', 'row_count': 0})
        status = 'unavailable_at_cutoff' if cutoff and not rows[ds] else state['status']
        statuses.append({**state, 'scope': 'current_collection_state', 'status': status,
            'stored_rows': len(rows[ds]), 'retained_old_values': bool(rows[ds]) and (state['status'] in {'failed', 'empty'}
                or bool((state.get('details') or {}).get('retained_old_values')))})
    financials = financial_series(_financial_observations(rows['financials']))
    quote = market['quote']
    currencies = {r['unit'] for r in rows['financials'] if re.fullmatch('[A-Z]{3}', r.get('unit', ''))}
    return {'security_id': sid, 'ticker': ticker, 'currency': quote.get('currency') or (next(iter(currencies)) if len(currencies) == 1 else None),
        'financials': financials, 'market': market, 'valuation': valuation_view(financials, market), 'statuses': statuses,
        'template': research_template(ticker),
        'guidance': {'records': guidance_records, 'comparisons': comparisons(guidance_records, financials, as_of=cutoff),
                     'status': 'ready' if guidance_records else 'not_recorded', 'consensus': '未接入一致预期'},
        'filings': sorted(rows['filings'], key=lambda r: (r['filing_date'], r['id']), reverse=True),
        'pit': {'strict': False, 'as_of': cutoff.isoformat() if cutoff else None,
                'scope': 'local_observation_versions', 'limitation': '同时检查首次本地观察和披露时间；来源回补不代表严格历史可用',
                'metadata_scope': '模板与采集状态为当前信息；历史视图隐藏当前观察卡；指引按首次观察与披露时间筛选'}}
