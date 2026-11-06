"""Guard tests for the real concept-card catalog `docs/knowledge` (v2.6 spec §4.6).

Reads the committed cards, so a code change that adds a rule / reason / state / tool / version number without
updating the cards fails here. Offline: no database."""
from investment_assistant.data import families as data_families
import pytest

import investment_assistant.core.stages as stages
import investment_assistant.core.texts as labels
from investment_assistant.analysis import rules
from investment_assistant.data import fetch_policy
from investment_assistant.execution import conditions, entry_rules
from investment_assistant.core.knowledge import DEFAULT_ROOT, load

REQUIRED_IDS = (
    # family (9)
    'family.bars', 'family.sec_filings', 'family.news', 'family.fred', 'family.universe',
    'family.earnings', 'family.fundamentals', 'family.sec_text', 'family.sector',
    # indicator (14)
    'indicator.atr14', 'indicator.moving_average', 'indicator.high_low', 'indicator.volume_ratio',
    'indicator.relative_strength', 'indicator.volatility_20', 'indicator.gap', 'indicator.breadth',
    'indicator.regime', 'indicator.sector_strength', 'indicator.hy_oas', 'indicator.t10y2y',
    'indicator.dollar_volume_20', 'indicator.screen_rs',
    # rule (10)
    'rule.trend_reclaim', 'rule.volume_breakout', 'rule.earnings_gap_up', 'rule.trend_break',
    'rule.earnings_gap_down', 'rule.position_drawdown', 'rule.new_filing', 'rule.abnormal_move',
    'rule.plan_stop', 'rule.plan_target',
    # plan (12)
    'plan.ref_price', 'plan.entry_zone', 'plan.stop', 'plan.target1', 'plan.trailing_stop', 'plan.validity',
    'plan.sizing', 'plan.earnings', 'plan.context_flags', 'plan.status', 'plan.derivation', 'plan.own',
    # agent (15)
    'agent.action_levels', 'agent.citation', 'agent.tool_calls', 'agent.thesis', 'agent.counter',
    'agent.invalidation', 'agent.review_when', 'agent.changes', 'agent.version_chain', 'agent.review_outcome',
    'agent.signals', 'agent.user_input', 'agent.reject_reasons', 'agent.brief', 'agent.legacy',
    # review (18)
    'review.r_multiple', 'review.plan_shadow', 'review.control_group', 'review.discipline_chase',
    'review.discipline_early_take', 'review.discipline_held_loser', 'review.discipline_unplanned',
    'review.tracks', 'review.track_status', 'review.version_basis', 'review.chain_state',
    'review.first_latest', 'review.weekly', 'review.monthly_return', 'review.process_check',
    'review.sensitivity', 'review.position', 'review.backcheck',
    # system (13)
    'system.gate_hold', 'system.cutoff', 'system.append_only', 'system.stages', 'system.stage_status',
    'system.pool_tiers', 'system.screen', 'system.decision_unit', 'system.conflict', 'system.decision',
    'system.push', 'system.versions', 'system.concept_card',
    # v2.7 growth loop (7)
    'review.retro_card', 'review.retro_end', 'system.my_knowledge', 'review.knowledge_performance',
    'agent.knowledge_citation', 'system.discussion', 'review.weekly_learning',
    # v2.8 (3): the spec calls the first data.fetch_policy, but card ids must start with an existing group (knowledge.GROUPS)
    'system.fetch_policy', 'system.daily_workflow', 'system.agent_connection',
    # v2.9 executable plans (7): six from Task 3, review.execution_status from Task 4
    'plan.entry_type', 'plan.open_scenarios', 'plan.max_hold', 'agent.retrigger', 'system.active_chain',
    'review.holding_status', 'review.execution_status',
    # v2.11 accounts and paper trading (7)
    'account.ledger', 'account.paper', 'account.agent_portfolio', 'account.follow_plans',
    'account.fill_rules', 'account.reconciliation', 'account.comparison',
    # v2.12–v2.15: the other fourteen new cards (four new families above; ATR is an indicator)
    'system.adapter_registry', 'system.backup_source', 'system.data_request', 'indicator.registry',
    'agent.company_researcher', 'agent.research_request', 'agent.research_dossier',
    'account.portfolio_check', 'agent.portfolio_manager', 'system.feature_flags',
    'system.roadmap_records', 'agent.optimize_proposal', 'agent.implementation_brief', 'system.layers',
)

STAGE_STATUSES = ('success', 'partial', 'degraded', 'failed', 'skipped')


@pytest.fixture(scope='module')
def catalog():
    return load(DEFAULT_ROOT)


def body_of(catalog, card_id: str) -> str:
    return catalog.concepts[card_id].body_md


def test_catalog_loads_and_matches_required_ids(catalog):
    assert len(REQUIRED_IDS) == 129 and len(set(REQUIRED_IDS)) == 129
    assert set(catalog.concepts) == set(REQUIRED_IDS)


def test_families_and_rules_have_cards(catalog):
    for family in data_families.FAMILIES:
        assert f'family.{family.id}' in catalog.concepts, family.id
    assert set(labels.RULE_TEXT) == set(rules.PRICE_RULES) | {'new_filing'} and len(labels.RULE_TEXT) == 10
    for rule_id, text in labels.RULE_TEXT.items():
        card = catalog.concepts.get(f'rule.{rule_id}')
        assert card is not None, rule_id
        assert card.title == text, rule_id
        assert card.applies_to == ('rules-1',), rule_id


CODE_SET_CASES = [
    ('conditions', sorted(conditions.CONDITIONS_ALL), ('agent.invalidation', 'agent.review_when')),
    ('stage_ids', list(stages.STAGE_IDS), ('system.stages',)),
    ('stage_status', list(STAGE_STATUSES), ('system.stage_status',)),
    ('retry_statuses', sorted(fetch_policy.RETRY_STATUSES), ('system.fetch_policy',)),
    ('fetch_error_kinds', list(fetch_policy.ERROR_KINDS), ('system.fetch_policy',)),
    ('entry_types', list(entry_rules.ENTRY_TYPES), ('plan.entry_type',)),
]


@pytest.mark.parametrize('name,items,card_ids', CODE_SET_CASES, ids=[case[0] for case in CODE_SET_CASES])
def test_code_sets_mentioned_in_cards(catalog, name, items, card_ids):
    assert items, name
    text = '\n'.join(body_of(catalog, card_id) for card_id in card_ids)
    missing = [item for item in items if f'`{item}`' not in text]
    assert not missing, f'{name}: 卡 {", ".join(card_ids)} 的正文里缺 {missing}'


def test_discipline_cards(catalog):
    for flag in ('chase', 'early_take', 'held_loser', 'unplanned'):
        assert f'review.discipline_{flag}' in catalog.concepts, flag


def test_principles_structure(catalog):
    principles = catalog.principles
    assert principles is not None
    headings = [section.heading for section in principles.sections]
    assert len(headings) == 18                       # thirteen nodes plus five explanatory sections
    assert headings[0] == '系统做什么、不做什么'
    assert headings[-1] == '怎么在系统里核对'
    staged = [section for section in principles.sections if section.stage]
    assert [section.stage for section in staged] == list(stages.STAGE_IDS)
    assert len({section.stage for section in staged}) == len(staged) == 13      # portfolio and propose included
    for section in staged:
        assert section.concepts, f'{section.heading}: 阶段小节没有链任何概念卡'
    assert [section.heading for section in principles.sections[14:17]] == ['你做什么：决定与成交', '从计划到成交', '复盘口径']
    first, last = principles.sections[1], principles.sections[13]
    assert first.stage == 'sec_monitor' and first.heading == '① SEC 监控' and 'family.sec_filings' in first.concepts
    assert last.stage == 'propose' and last.heading == '⑬ 优化提案'
    assert set(last.concepts) == {'agent.optimize_proposal', 'system.roadmap_records', 'agent.implementation_brief'}
    retro = principles.sections[12]
    assert retro.stage == 'retro' and retro.heading == '⑫ 复盘'
    assert set(retro.concepts) == {'review.retro_card', 'review.retro_end', 'review.weekly'}
    assert principles.sections[11].stage == 'push' and principles.sections[2].stage == 'collect'
    portfolio = principles.sections[9]
    assert portfolio.stage == 'portfolio' and portfolio.heading == '⑨ 组合检查'
    assert set(portfolio.concepts) == {'account.portfolio_check', 'agent.portfolio_manager'}
    accounts = principles.sections[10]
    assert accounts.stage == 'accounts' and accounts.heading == '⑩ 账户与模拟交易'
    assert set(accounts.concepts) == {'account.ledger', 'account.paper', 'account.agent_portfolio',
                                     'account.follow_plans', 'account.fill_rules', 'account.reconciliation',
                                     'account.comparison'}
