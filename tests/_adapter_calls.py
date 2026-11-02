"""One adapter call (the single dispatch path, R23) for the fetch-1 call-site tests.

The per-family fetch functions these tests used to call are gone: every fetch is ``registry.run_adapter``. This helper
builds the host's ``AdapterDeps`` the way a test needs it (fetchers, the recorded sleep, the possibly patched ``_tx``)
and hands back the tally plus what each target's store returned, so the old assertions (attempts, error kinds, rows,
latest, summaries) read the same facts from the same places the daily node reads them."""
from types import SimpleNamespace

import investment_assistant.core.tx as core_tx
import investment_assistant.data.collect as collect
from investment_assistant.data import registry
from investment_assistant.data.adapter_types import AdapterDeps
from investment_assistant.data.sources.edgar import COMPANY_TICKERS_TARGET


def adapter_run(adapter_id, targets, *, session, now, features=None, sleep=None, scope='daily', **deps):
    """-> ``SimpleNamespace(tally, stored)``; ``stored[target] = (source id, fetched rows, store result)``."""
    stored = {}
    projected = AdapterDeps(transaction=core_tx._tx, sleep=sleep if sleep is not None else collect.FETCH_SLEEP, **deps)
    tally = registry.run_adapter(
        registry.get(adapter_id), targets, scope=scope, trigger='scheduled', run_id=None, session=session, now=now,
        deps=projected, features=features or {},
        observe=lambda target, source, rows, result: stored.__setitem__(target, (source, rows, result)),
        whole_call_target=COMPANY_TICKERS_TARGET if adapter_id == 'sec_filings' else None)
    return SimpleNamespace(tally=tally, stored=stored)


def item(run, target):
    """The ``detail.targets`` item the run booked for ``target``."""
    return run.tally.record_for(target)
