"""Read-only account projections and explicitly requested account commands."""
from investment_assistant.execution.paper import accounts as execution_paper_accounts
from datetime import UTC, datetime

from fastapi import APIRouter, Body, Query

from investment_assistant.api.routes.workbench import _call, _date
from investment_assistant.views import account as account_views
from investment_assistant.workflow.nodes import accounts as paper_accounts
from investment_assistant.core import tx as service

router = APIRouter(prefix='/api/accounts')


def _now():
    return datetime.now(UTC)


def _run(fn, *args, **kwargs):
    return service._jsonable(_call(fn, *args, **kwargs))


@router.get('')
def overview(include_closed: bool = False):
    return _run(account_views.overview, now=_now(), include_closed=include_closed)


@router.post('')
def create(payload: dict = Body(default_factory=dict)):
    return _run(execution_paper_accounts.create_paper_account, payload, now=_now())


# Literal route must precede /{account_id}.
@router.get('/compare')
def compare(from_: str | None = Query(default=None, alias='from')):
    return _run(account_views.compare, now=_now(), from_session=_date(from_, 'from'))


@router.get('/{account_id}')
def detail(account_id: str):
    return _run(account_views.detail, account_id, now=_now())


@router.post('/{account_id}/opening')
def opening(account_id: str, payload: dict = Body(default_factory=dict)):
    return _run(execution_paper_accounts.open_real_account, account_id, payload, now=_now())


@router.post('/{account_id}/params')
def params(account_id: str, payload: dict = Body(default_factory=dict)):
    return _run(execution_paper_accounts.change_params, account_id, payload, now=_now())


@router.post('/{account_id}/close')
def close(account_id: str, payload: dict = Body(default_factory=dict)):
    return _run(execution_paper_accounts.close, account_id, payload, now=_now())


@router.get('/{account_id}/equity')
def equity(account_id: str, from_: str | None = Query(default=None, alias='from'), to: str | None = None):
    return _run(account_views.equity, account_id, now=_now(),
                from_session=_date(from_, 'from'), to_session=_date(to, 'to'))


@router.get('/{account_id}/portfolio-check')
def portfolio_check(account_id: str, session: str | None = None):
    return _run(account_views.portfolio_check, account_id, now=_now(), session=_date(session, 'session'))


@router.get('/{account_id}/trades')
def trades(account_id: str):
    return _run(account_views.trades, account_id, now=_now())


@router.get('/{account_id}/orders')
def orders(account_id: str, status: str | None = None):
    return _run(account_views.orders, account_id, now=_now(), status=status)


@router.get('/{account_id}/decisions')
def decisions(account_id: str, session: str | None = None):
    return _run(account_views.decisions, account_id, now=_now(), session=_date(session, 'session'))
