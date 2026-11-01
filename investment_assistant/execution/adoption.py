"""Adoption derived from fills `execution-1` (v2.9 spec §6.1, §6.2, D21–D23, D28; R13, R17). Pure functions.

There is no decision button any more: a fill that points at a plan version (`trades.plan_id`) is the proof of
adoption. This module maps fills to plans and plans to chains, and says what happened to a plan version:

- `chain_of_plan`: which chain a plan belongs to (D22). An `agent-2` / `agent-3` version is on its own chain;
  a rule baseline or `own` plan belongs to its unit: the chain the unit opened itself (R17: that wins over the
  merge record), else the chain it was merged into (`chain_retriggers`), else none.
- `chain_buys`: the live buys of a chain = fills linked through `plan_id` plus the old `decision_id` route,
  de-duplicated by trade id.
- `status` / `chain_status`: `adopted`, `out_of_validity`, `pending`, `not_executed` per version; a chain with
  any linked buy is `executed` (R13).
- `unplanned`: a buy with no plan link (and no plan through an old decision).

Trades are dicts with `id`, `ticker`, `side`, `executed_at`, `voided_at`, `plan_id`, `decision_id`; plan rows
are `trade_plans` rows. Ids are compared as strings, so UUID objects and text mix freely.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

EXECUTION_VERSION = 'execution-1'

NY = ZoneInfo('America/New_York')

STATUSES = ('adopted', 'out_of_validity', 'pending', 'not_executed')
CHAIN_STATUSES = ('executed', 'pending', 'not_executed')
# Execution status wording (spec §6.2, the page column); the frontend keeps a copy and a test guards it.
EXECUTION_TEXT = {
    'adopted': '已采纳（{date} 成交 {quantity} 股）',
    'out_of_validity': '有效期外成交',
    'pending': '入场期，未成交',
    'not_executed': '未执行',
    'executed': '已执行',
}

# Shared with the read views of Task 4 (spec §6.1): how many sessions back an `own` plan stays linkable.
LINKABLE_OWN_SESSIONS = 5
NOTE_MAX_CHARS = 1000


def _key(value: Any) -> str | None:
    return None if value is None else str(value)


def _session(ts: Any) -> date:
    return ts.astimezone(NY).date() if hasattr(ts, 'astimezone') else ts


# ---------------------------------------------------------------- plan -> chain

def chain_of_plan(plan: dict, *, versions_by_plan: dict, retriggers: list[dict],
                  chains: list[dict]) -> tuple[str, date] | None:
    """`(ticker, chain signal_date)` of the chain a plan row belongs to, or None (spec D22).

    versions_by_plan: `agent_plan_versions` rows keyed by plan id (only agent chain versions are in it);
    retriggers: `chain_retriggers` rows (`ticker`, `unit_signal_date`, `chain_signal_date`); chains: the
    ticker's chains as dicts with `ticker` and `signal_date`.
    """
    ticker = plan['ticker']
    version = _lookup(versions_by_plan, plan['id'])
    if version is not None:
        return ticker, version.get('signal_date', plan['signal_date'])
    unit = plan['signal_date']
    if any(c['ticker'] == ticker and c['signal_date'] == unit for c in chains):
        return ticker, unit
    for row in retriggers:
        if row['ticker'] == ticker and row['unit_signal_date'] == unit:
            return ticker, row['chain_signal_date']
    return None


def _lookup(mapping: dict, key: Any) -> Any:
    if key in mapping:
        return mapping[key]
    wanted = _key(key)
    return next((value for k, value in mapping.items() if _key(k) == wanted), None)


# ---------------------------------------------------------------- fills -> chain

def chain_buys(chain_key: tuple[str, date], trades: list[dict], *, plan_chain: dict,
               legacy_decision_ids: set) -> list[dict]:
    """The chain's live buys, oldest first: linked through `plan_id` (the chain `plan_chain` maps the plan to)
    united with the old route (`decision_id` among the unit's decisions), de-duplicated by trade id."""
    ticker = chain_key[0]
    legacy = {_key(i) for i in legacy_decision_ids}
    mapped = {_key(k): v for k, v in plan_chain.items()}
    seen: set[str | None] = set()
    out: list[dict] = []
    for trade in sorted(trades, key=lambda t: (t['executed_at'], str(t['id']))):
        if trade.get('side', 'buy') != 'buy' or trade.get('voided_at') is not None or trade['ticker'] != ticker:
            continue
        linked = trade.get('plan_id') is not None and mapped.get(_key(trade['plan_id'])) == chain_key
        old = trade.get('decision_id') is not None and _key(trade['decision_id']) in legacy
        if (linked or old) and _key(trade['id']) not in seen:
            seen.add(_key(trade['id']))
            out.append(trade)
    return out


def unplanned(trade: dict, *, legacy_plan_id: Any) -> bool:
    """A buy with no plan link and, through an old decision, no plan either (spec §6.2 「计划外」)."""
    if trade.get('side', 'buy') != 'buy':
        return False
    return trade.get('plan_id') is None and legacy_plan_id is None


# ---------------------------------------------------------------- status

def status(version_plan: dict, buys: list[dict], *, session: date, chain_ended: bool,
           chain_in_entry: bool = True) -> str:
    """Execution state of one plan version (spec §6.2).

    `buys` are the chain's buys; only those linked to this version (`plan_id`) count. A linked fill on a New
    York day inside [valid_from, valid_until] adopts the version; all linked fills outside it are
    `out_of_validity`. Without a linked fill: `pending` only while the session is before valid_until, the chain
    has not ended and the chain is still in its entry phase (`chain_in_entry`; pass False for a holding or
    exiting chain, whose revisions are never waiting for a fill), else `not_executed`.
    """
    mine = [b for b in buys if b.get('voided_at') is None and _key(b.get('plan_id')) == _key(version_plan['id'])]
    if mine:
        inside = [b for b in mine if version_plan['valid_from'] <= _session(b['executed_at'])
                  <= version_plan['valid_until']]
        return 'adopted' if inside else 'out_of_validity'
    if session < version_plan['valid_until'] and not chain_ended and chain_in_entry:
        return 'pending'
    return 'not_executed'


def chain_status(versions: list[dict], buys: list[dict], *, session: date, chain_ended: bool,
                 chain_in_entry: bool = True) -> str:
    """Chain-level state (R13): any live linked buy on the chain is `executed` (also when the fill is linked to
    the unit's rule baseline, which keeps a merged unit's chain from reading 「未执行」); otherwise the newest
    version's `pending` / `not_executed`. `versions` are the chain's version plan rows, oldest first."""
    if any(b.get('voided_at') is None for b in buys):
        return 'executed'
    if not versions:
        return 'not_executed'
    latest = max(versions, key=lambda v: v.get('version_no', 0))
    return status(latest, [], session=session, chain_ended=chain_ended, chain_in_entry=chain_in_entry)


def status_text(code: str, *, buys: list[dict] | None = None, version_plan: dict | None = None) -> str:
    """The page wording of a status; `adopted` names the first inside fill's date and size."""
    template = EXECUTION_TEXT[code]
    if code != 'adopted':
        return template
    plan_id = _key(version_plan['id']) if version_plan else None
    fills = [b for b in (buys or []) if b.get('voided_at') is None and _key(b.get('plan_id')) == plan_id]
    if version_plan:
        fills = [b for b in fills if version_plan['valid_from'] <= _session(b['executed_at'])
                 <= version_plan['valid_until']] or fills
    if not fills:
        return template.format(date='—', quantity='—')
    first = fills[0]
    return template.format(date=_session(first['executed_at']).isoformat(),
                           quantity=format(Decimal(str(first['quantity'])).normalize(), 'f'))


def followed(buys: list[dict], *, plans_by_id: dict, version_no_by_plan: dict) -> dict | None:
    """What the first live linked buy followed (R13 「照什么执行」): `{kind, version_no, plan_id, text}` with text
    `Agent 第 n 版` / `规则基线` / `自定`, or None when no buy carries a known plan."""
    for trade in sorted((b for b in buys if b.get('voided_at') is None),
                        key=lambda t: (t['executed_at'], str(t['id']))):
        plan = _lookup(plans_by_id, trade.get('plan_id')) if trade.get('plan_id') is not None else None
        if plan is None:
            continue
        kind = plan.get('kind')
        version_no = _lookup(version_no_by_plan, plan['id'])
        if kind == 'agent' and version_no is not None:
            text = f'Agent 第 {version_no} 版'
        elif kind == 'rule':
            text = '规则基线'
        elif kind == 'own':
            text = '自定'
        else:
            text = str(kind)
        return {'kind': kind, 'version_no': version_no, 'plan_id': str(plan['id']), 'text': text}
    return None
