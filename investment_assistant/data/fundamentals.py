"""US security identity and version-preserving SEC Company Facts normalization."""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict

from investment_assistant.data.identity import stock_id


class StrictModel(BaseModel):
    """Strict payloads: no extra keys, no NaN. Inherited from the removed research cards."""

    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


def valid_source_link(value: str) -> str:
    if value and (urlparse(value).scheme not in {'http', 'https'} or not urlparse(value).netloc):
        raise ValueError('来源链接须为http/https原文链接')
    return value


# Separate attribution and investment scopes are intentional, not interchangeable aliases.
CONCEPTS = {
    'revenue': ('RevenueFromContractWithCustomerExcludingAssessedTax', 'Revenues', 'SalesRevenueNet', 'SalesRevenueGoodsNet'),
    'gross_profit': ('GrossProfit',),
    'cost_of_revenue': ('CostOfRevenue', 'CostOfGoodsAndServicesSold', 'CostOfGoodsSold'),
    'operating_income': ('OperatingIncomeLoss',),
    'net_income': ('NetIncomeLoss',),
    'consolidated_net_income': ('ProfitLoss',),
    'common_net_income': ('NetIncomeLossAvailableToCommonStockholdersBasic',),
    'eps_diluted': ('EarningsPerShareDiluted',),
    'cfo': ('NetCashProvidedByUsedInOperatingActivities',),
    'cash_capex': ('PaymentsToAcquirePropertyPlantAndEquipment',),
    'broad_capex': ('PaymentsToAcquireProductiveAssets',),
    'sbc': ('ShareBasedCompensation',),
    'cash': ('CashAndCashEquivalentsAtCarryingValue',),
    'investments': ('ShortTermInvestments',),
    'debt': (),  # Derived only from mutually exclusive complete components.
    'long_term_debt_total': ('LongTermDebt',),
    'long_term_debt': ('LongTermDebtNoncurrent',),
    'debt_with_leases_noncurrent': ('LongTermDebtAndCapitalLeaseObligations', 'LongTermDebtAndFinanceLeaseObligationsNoncurrent'),
    'debt_with_leases_current': ('DebtCurrent',),
    'long_term_debt_with_leases_total': ('LongTermDebtAndFinanceLeaseObligationsIncludingCurrentMaturities',),
    'short_term_debt': ('LongTermDebtCurrent',),
    'short_term_borrowings': ('ShortTermBorrowings',),
    'receivables': ('AccountsReceivableNetCurrent',),
    'inventory': ('InventoryNet',),
    'shares': ('CommonStockSharesOutstanding',),
    'weighted_shares': ('WeightedAverageNumberOfDilutedSharesOutstanding',),
    'repurchases': ('PaymentsForRepurchaseOfCommonStock',),
}
INSTANT = frozenset({'cash', 'investments', 'debt', 'long_term_debt', 'short_term_debt',
                     'short_term_borrowings', 'receivables', 'inventory', 'shares', 'long_term_debt_total',
                     'debt_with_leases_noncurrent', 'debt_with_leases_current', 'long_term_debt_with_leases_total'})
NON_ADDITIVE = INSTANT | {'eps_diluted', 'weighted_shares'}


def finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if math.isfinite(value) else None
    except OverflowError:
        return None


def canonical_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def observation_id(row: dict) -> str:
    # Caller-supplied ids are storage identities; normalized ids bind source/version/value.
    return row.get('id') or canonical_hash({k: row.get(k) for k in (
        'metric', 'value', 'unit', 'basis', 'start', 'end', 'accession', 'filed', 'concept', 'source_locator', 'source_hash')})


def default_forms(ticker: str) -> list[str]:
    # Preserve EdgarSource.discover's existing ticker-specific rule; do not change monitor behavior.
    return ['10-Q', '8-K'] if ticker == 'TSLA' else ['10-Q', '10-K', '8-K']


def normalize_companyfacts(payload: dict, *, ticker: str, allowed_forms: list[str] | None = None) -> list[dict]:
    ticker = stock_id(ticker).split(':')[-1]
    forms = set(default_forms(ticker) if allowed_forms is None else allowed_forms)
    cik = int(payload['cik'])
    facts = payload.get('facts', {}).get('us-gaap', {})
    if not isinstance(facts, dict):
        raise ValueError('Invalid SEC facts structure')
    rows = []
    # A standalone normalized payload has canonical-JSON provenance. The HTTP adapter replaces
    # this with the exact response-byte hash and recomputes ids before returning observations.
    clean_payload = _finite_json(payload)
    source_hash = canonical_hash(clean_payload)
    for metric, concepts in CONCEPTS.items():
        for concept in concepts:
            for unit, records in facts.get(concept, {}).get('units', {}).items():
                valid_unit = (unit == 'shares' if metric in {'shares', 'weighted_shares'} else
                              bool(re.fullmatch(r'[A-Z]{3}/shares', unit)) if metric == 'eps_diluted' else
                              bool(re.fullmatch(r'[A-Z]{3}', unit)))
                if not valid_unit:
                    continue
                for index, raw in enumerate(records):
                    value = finite(raw.get('val'))
                    if value is None or raw.get('form') not in forms:
                        continue
                    try:
                        end = date.fromisoformat(raw['end']).isoformat()
                        start = date.fromisoformat(raw['start']).isoformat() if raw.get('start') else None
                        filed = date.fromisoformat(raw['filed']).isoformat()
                    except (ValueError, TypeError, KeyError):
                        continue
                    accession = raw.get('accn', '')
                    if not re.fullmatch(r'\d{10}-\d{2}-\d{6}', accession) or start and start > end:
                        continue
                    basis = {'net_income': 'us-gaap:parent', 'common_net_income': 'us-gaap:common'}.get(metric, 'us-gaap:consolidated')
                    if metric in {'eps_diluted', 'weighted_shares', 'shares'}:
                        basis = 'us-gaap:reported_share_basis_unverified'
                    row = dict(metric=metric, value=value, unit=unit, basis=basis, start=start, end=end,
                               period_kind='duration' if start else 'instant', form=raw['form'],
                               accession=accession, filed=filed,
                               source_url=f'https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace("-", "")}/{accession}-index.html',
                               concept=concept, source_locator=f'/facts/us-gaap/{concept}/units/{unit.replace("~", "~0").replace("/", "~1")}/{index}',
                               source_hash=source_hash, fiscal_year_reported=raw.get('fy'),
                               fiscal_period_reported=raw.get('fp'), frame=raw.get('frame'))
                    row['id'] = observation_id(row)
                    rows.append(row)
    return sorted(rows, key=lambda r: (r['end'], r['metric'], r['start'] or '', r['filed'], r['id']))


def _finite_json(value):
    if isinstance(value, float):
        return finite(value)
    if isinstance(value, dict):
        return {k: _finite_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_finite_json(v) for v in value]
    return value
