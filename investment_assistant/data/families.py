"""Data-menu catalog and coverage projections of the authoritative adapters."""
from investment_assistant.data.registry import ADAPTERS, get as get
from investment_assistant.data.fetch_status import classify as classify
from investment_assistant.data.adapters import universe

EVIDENCE = '这里只展示采集事实。所有规则都未经验证（Phase 0 Gate HOLD）：数据完整不代表信号有效。'
FAMILIES = ADAPTERS
RECORDED = tuple(adapter.id for adapter in ADAPTERS)
COVERAGE_NOTES = {adapter.id: adapter.coverage_note for adapter in ADAPTERS}


def lagging(family_id, latest, *, session):
    return get(family_id).lagging(latest, session)


def coverage(family_id, targets, latest, *, session, universe_members=None):
    """FRED's observed coverage is independent of staleness; universe counts members."""
    adapter = get(family_id)
    lagged = [target for target in targets if adapter.lagging(latest.get(target), session)]
    if family_id == 'universe':
        members = (len(universe_members if universe_members is not None else universe.load().tickers)
                   if latest.get('nasdaq100') is not None and 'nasdaq100' in targets else 0)
        return {'targets': members, 'covered': members, 'lagging': lagged}
    covered = (sum(latest.get(target) is not None for target in targets) if family_id == 'fred'
               else len(targets) - len(lagged))
    return {'targets': len(targets), 'covered': covered, 'lagging': lagged}
