"""Guard tests for the real concept-card catalog `docs/knowledge` (v2.6 spec §4.6).

Reads the committed cards, so a code change that adds a rule / entry type without updating the cards fails here.
Offline: no database."""

import re
from collections import Counter

import pytest

import investment_assistant.core.texts as labels
from investment_assistant.analysis import rules
from investment_assistant.core.knowledge import DEFAULT_ROOT, REPO_ROOT, load
from investment_assistant.execution import entry_rules

REQUIRED_IDS = (
    # indicator (15)
    "indicator.atr14",
    "indicator.moving_average",
    "indicator.high_low",
    "indicator.volume_ratio",
    "indicator.relative_strength",
    "indicator.volatility_20",
    "indicator.gap",
    "indicator.breadth",
    "indicator.regime",
    "indicator.sector_strength",
    "indicator.hy_oas",
    "indicator.t10y2y",
    "indicator.dollar_volume_20",
    "indicator.screen_rs",
    "indicator.registry",
    # rule (10)
    "rule.trend_reclaim",
    "rule.volume_breakout",
    "rule.earnings_gap_up",
    "rule.trend_break",
    "rule.earnings_gap_down",
    "rule.position_drawdown",
    "rule.new_filing",
    "rule.abnormal_move",
    "rule.plan_stop",
    "rule.plan_target",
    # plan (15)
    "plan.ref_price",
    "plan.entry_zone",
    "plan.stop",
    "plan.target1",
    "plan.trailing_stop",
    "plan.validity",
    "plan.sizing",
    "plan.earnings",
    "plan.context_flags",
    "plan.status",
    "plan.derivation",
    "plan.own",
    "plan.entry_type",
    "plan.open_scenarios",
    "plan.max_hold",
    "plan.research",
    "plan.retrospect",
)


@pytest.fixture(scope="module")
def catalog():
    return load(DEFAULT_ROOT)


def body_of(catalog, card_id: str) -> str:
    return catalog.concepts[card_id].body_md


def test_catalog_loads_and_matches_required_ids(catalog):
    assert len(REQUIRED_IDS) == 42 and len(set(REQUIRED_IDS)) == 42
    assert set(catalog.concepts) == set(REQUIRED_IDS)
    assert Counter(card.group for card in catalog.concepts.values()) == {"indicator": 15, "rule": 10, "plan": 17}


def test_rules_have_cards(catalog):
    assert set(labels.RULE_TEXT) == set(rules.PRICE_RULES) | {"new_filing"} and len(labels.RULE_TEXT) == 10
    for rule_id, text in labels.RULE_TEXT.items():
        card = catalog.concepts.get(f"rule.{rule_id}")
        assert card is not None, rule_id
        assert card.title == text, rule_id
        assert card.applies_to == ("rules-1",), rule_id


CODE_SET_CASES = [
    ("entry_types", list(entry_rules.ENTRY_TYPES), ("plan.entry_type",)),
]


@pytest.mark.parametrize("name,items,card_ids", CODE_SET_CASES, ids=[case[0] for case in CODE_SET_CASES])
def test_code_sets_mentioned_in_cards(catalog, name, items, card_ids):
    assert items, name
    text = "\n".join(body_of(catalog, card_id) for card_id in card_ids)
    missing = [item for item in items if f"`{item}`" not in text]
    assert not missing, f"{name}: 卡 {', '.join(card_ids)} 的正文里缺 {missing}"


def test_every_related_and_body_link_resolves_within_kept_cards(catalog):
    """每张保留卡的 related 都指向保留卡；正文里指向概念卡的相对链接都指向存在的文件。"""
    card_dir = DEFAULT_ROOT / "concepts"
    for card in catalog.concepts.values():
        for target in card.related:
            assert target in catalog.concepts, f"{card.id}: related 里的 {target} 不是保留卡"
        for href in re.findall(r"\]\(([^)\s]+\.md)\)", card.body_md):
            assert (card_dir / href).resolve().is_file(), f"{card.id}: 正文链接 {href} 指向的文件不存在"


def test_every_source_path_exists(catalog):
    """每张保留卡 sources 里的路径都在仓库里存在。"""
    for card in catalog.concepts.values():
        for path in card.sources:
            assert (REPO_ROOT / path).exists(), f"{card.id}: sources 里的 {path} 不存在"
