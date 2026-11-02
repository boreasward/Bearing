"""Pure SIC classification; unknown companies do not get a synthetic industry."""
import json
from pathlib import Path

from investment_assistant.data import repo
from investment_assistant.data.adapter_types import Source
from investment_assistant.data.adapter_support import ONCE, make_adapter, sec_outcome, sec_source
from investment_assistant.data.sources.edgar import fetch_sic

CONFIG_DIR = Path(__file__).resolve().parents[3] / 'config'


def classify(sic: str | None, *, groups: dict, overrides: dict, ticker) -> str | None:
    if ticker in overrides:
        return overrides[ticker]
    return groups.get(sic[:2]) if sic else None


def _fetch(target, *, session, start, end, deps):
    source = sec_source(deps)
    def fetch():
        answer = fetch_sic(target, deps=deps)
        groups = json.loads((CONFIG_DIR / 'sic_groups.json').read_text())
        overrides = json.loads((CONFIG_DIR / 'sector_overrides.json').read_text())
        return [{**answer, 'group': classify(answer['sic'], groups=groups, overrides=overrides, ticker=target)}]
    return sec_outcome(source, fetch)


def _store(conn, rows, *, target, source_id, now):
    answer = rows[0]
    old = repo.latest_sectors(conn, [target], cutoff_at=now).get(target)
    if old is not None and all(old.get(k) == answer.get(k) for k in ('sic', 'sic_description', 'group')):
        return {'inserted': 0, 'existing': 1, 'latest': old['fetched_at']}
    repo.insert_sector_classification(conn, {**answer, 'ticker': target, 'source': source_id, 'fetched_at': now})
    return {'inserted': 1, 'existing': 0, 'latest': now}


def _latest(conn, targets, *, cutoff_at):
    return {t: row['fetched_at'] for t, row in repo.latest_sectors(conn, targets, cutoff_at=cutoff_at).items()}


ADAPTER = make_adapter('sector', '行业分类', 'SEC SIC分类；无分类保持None', 'sector_classifications',
    (Source('sec', _fetch, ONCE, 'SIC→配置覆盖→两位组；只在答案变化时追加'),),
    _store, _latest, days=90, coverage_note='覆盖 = 最新行业答案在session前90天内采集；缺分类保留None。')
