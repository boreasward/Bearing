"""Account display vocabulary agrees with the pure Python owners; offline, no ledger or network."""
import re
from pathlib import Path


from investment_assistant.core import texts
from investment_assistant.execution import accounts
from investment_assistant.execution import entry_rules
from investment_assistant.execution.paper import fill as paper_fill
from investment_assistant.execution.paper import strategies as paper_strategies

ROOT = Path(__file__).resolve().parents[1]
ACCOUNTS_TS = ROOT / 'web/src/lib/accounts.ts'
STATUS_TS = ROOT / 'web/src/lib/ui/domain/status.ts'
MIGRATION = ROOT / 'migrations/030_accounts.sql'


# The front end's rejection words (and the portfolio reasons) gain the v2.14 sector / exposure codes with Task 5; Task 7
# removes this marker (removal list: Task 4 report, Segment F).


def _table(name: str) -> dict[str, str]:
    match = re.search(rf'export const {name}\b[^=]*=\s*\{{(.*?)\}} as const', ACCOUNTS_TS.read_text(), re.S)
    assert match, f'frontend {name} missing'
    return dict(re.findall(r"(\w+):\s*'([^']*)'", match.group(1)))


def _sql_statuses(table: str) -> set[str]:
    block = re.search(rf'CREATE TABLE IF NOT EXISTS {table} \((.*?)\n\);', MIGRATION.read_text(), re.S)
    assert block, table
    check = re.search(r"\bstatus\s+TEXT[^\n]*?CHECK\s*\(status IN \(([^)]*)\)\)", block.group(1), re.S)
    assert check, f'{table}.status CHECK missing'
    return set(re.findall(r"'([^']+)'", check.group(1)))


def test_strategy_and_real_account_identity_match():
    assert _table('STRATEGY_TEXT') == accounts.STRATEGY_TEXT
    assert set(_table('STRATEGY_TEXT')) == set(accounts.STRATEGIES)
    assert f"export const REAL_ACCOUNT_ID = '{accounts.REAL_ACCOUNT_ID}'" in ACCOUNTS_TS.read_text()


def test_paper_fill_enum_sets_match():
    assert set(_table('FILL_CASE_TEXT')) == set(paper_fill.FILL_CASES)
    assert set(_table('EXIT_REASON_TEXT')) == set(paper_fill.EXIT_REASONS)


def test_rejection_codes_backend_vocabulary():
    """v2.14 adds the sector / exposure codes; the text table, the code set and the shared table agree."""
    assert {'sector_cap', 'max_per_sector', 'single_cap', 'total_cap', 'held_ticker'} <= set(paper_strategies.REJECT_CODES)
    assert set(paper_strategies.REJECT_TEXT) == set(paper_strategies.REJECT_CODES) == set(texts.REJECT_TEXT)
    assert set(texts.PORTFOLIO_REASON_TEXT) >= set(texts.REJECT_TEXT) | {'sector_unknown', 'deferred_by_cash'}


def test_rejection_enum_set_matches_frontend():
    assert set(_table('REJECT_TEXT')) == set(paper_strategies.REJECT_CODES)


def test_shared_chinese_display_maps_match_backend():
    owners = {'ORDER_STATUS_TEXT': accounts, 'EXIT_REASON_TEXT': paper_fill,
              'FILL_CASE_TEXT': paper_fill, 'RECONCILE_TEXT': accounts}
    for name, owner in owners.items():
        assert hasattr(owner, name), f'backend {owner.__name__}.{name} missing: display maps must be shared and verified'
        assert _table(name) == getattr(owner, name), name
    assert hasattr(paper_strategies, 'REJECT_TEXT')


def test_rejection_display_map_matches_frontend():
    assert _table('REJECT_TEXT') == paper_strategies.REJECT_TEXT


def test_cancel_void_and_chain_done_reason_tables_match_across_languages():
    expected = {
        'CANCEL_REASON_TEXT': {'superseded_by_revision', 'chain_ended', 'agent_cancel', 'validity_expired', 'account_closed'},
        'VOID_REASON_TEXT': set(entry_rules.VOID_REASONS),
        'CHAIN_DONE_TEXT': {'entry_voided', 'exited'},
    }
    assert expected['VOID_REASON_TEXT'] == {'gap_below_stop', 'gap_above_zone', 'gap_below_zone'}
    for name, keys in expected.items():
        backend = getattr(paper_fill, name)
        assert set(backend) == keys, name
        assert _table(name) == backend, name


def test_new_status_domains_cover_account_and_paper_enums():
    text = STATUS_TS.read_text()
    assert set(_table('ORDER_STATUS_TEXT')) == _sql_statuses('paper_orders')
    expected = {'account': _sql_statuses('accounts'), 'paper_order': _sql_statuses('paper_orders'),
                'paper_decision': _sql_statuses('paper_decisions'), 'reconcile': set(_table('RECONCILE_TEXT'))}
    for domain, keys in expected.items():
        match = re.search(rf'^\s*{domain}:\s*\{{([^\n]*)\}},?$', text, re.M)
        assert match, domain
        assert set(re.findall(r"(\w+):\s*'\w+'", match.group(1))) == keys, domain
