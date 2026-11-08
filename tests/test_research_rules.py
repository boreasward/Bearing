"""Research-condition pure functions (spec §6.3–§6.4): the four condition kinds, their
verbatim text, the met check against daily bars and the one-day research review."""

from datetime import date
from decimal import Decimal as D

import pytest

from investment_assistant.execution import research


def _bars(*closes, low=None):
    """日线到当天为止（最后一根是当天）；low 缺省等于当天收盘。"""
    return [
        {
            "price_date": date(2026, 10, 1 + i),
            "open": D(str(c)),
            "high": D(str(c)),
            "low": D(str(c)),
            "close": D(str(c)),
        }
        for i, c in enumerate(closes)
    ]


def test_validate_condition_kinds_and_errors():
    # 四种通过：level 三种带正数价位，均线一种带 20/50/200 之一
    assert research.validate_condition({"kind": "close_above", "level": "38.00"}) == {
        "kind": "close_above",
        "level": D("38.00"),
        "ma": None,
    }
    assert research.validate_condition({"kind": "close_below", "level": 30}) == {
        "kind": "close_below",
        "level": D(30),
        "ma": None,
    }
    assert research.validate_condition({"kind": "touch_below", "level": D("35.00")}) == {
        "kind": "touch_below",
        "level": D("35.00"),
        "ma": None,
    }
    assert research.validate_condition({"kind": "close_above_ma", "ma": 50}) == {
        "kind": "close_above_ma",
        "level": None,
        "ma": 50,
    }
    # 非 dict、未知 kind、负 level、ma 不在 20/50/200 → ValueError 以 invalid_condition 开头
    for bad in (
        None,
        "close_above",
        {"kind": "close_above_x", "level": 38},
        {"kind": "close_above"},
        {"kind": "close_above", "level": -1},
        {"kind": "close_above", "level": 0},
        {"kind": "close_above", "level": "NaN"},
        {"kind": "close_below", "level": "Infinity"},
        {"kind": "close_above_ma"},
        {"kind": "close_above_ma", "ma": 30},
    ):
        with pytest.raises(ValueError, match="invalid_condition"):
            research.validate_condition(bad)
    assert research.CONDITION_KINDS == ("close_above", "close_below", "touch_below", "close_above_ma")
    assert research.MA_CHOICES == (20, 50, 200)
    assert research.VERDICTS == ("ready", "watch", "reject")
    assert research.RESEARCH_STATUS_TEXT["watching"] == "观察中"


def test_condition_text_verbatim():
    assert research.condition_text({"kind": "close_above", "level": D("38.00")}) == "收盘站上 38.00"
    assert research.condition_text({"kind": "close_below", "level": D("30.00")}) == "收盘跌破 30.00"
    assert research.condition_text({"kind": "touch_below", "level": D("35.00")}) == "最低价触及 35.00 或以下"
    assert research.condition_text({"kind": "close_above_ma", "ma": 50}) == "收盘站上 50 日均线"


def test_condition_met_each_kind_hit_miss_and_short_history():
    # close_above：close ≥ level
    assert research.condition_met({"kind": "close_above", "level": D("38.00")}, _bars(37.00, 38.00)) is True
    assert research.condition_met({"kind": "close_above", "level": D("38.00")}, _bars(37.00, 37.99)) is False
    # close_below：close ≤ level
    assert research.condition_met({"kind": "close_below", "level": D("30.00")}, _bars(31.00, 30.00)) is True
    assert research.condition_met({"kind": "close_below", "level": D("30.00")}, _bars(31.00, 30.01)) is False
    # touch_below：low ≤ level
    touch_hit = [{"price_date": date(2026, 11, 4), "close": D("36.00"), "low": D("34.80")}]
    touch_miss = [{"price_date": date(2026, 11, 4), "close": D("36.00"), "low": D("35.01")}]
    assert research.condition_met({"kind": "touch_below", "level": D("35.00")}, touch_hit) is True
    assert research.condition_met({"kind": "touch_below", "level": D("35.00")}, touch_miss) is False
    # close_above_ma：MA = 最后 ma 根收盘的算术平均；日线不足 ma 根 → None
    ma20 = {"kind": "close_above_ma", "ma": 20}
    twenty = _bars(*([38.00] * 19 + [40.00]))  # MA = (19×38 + 40)/20 = 38.10，收盘 40 站上
    assert research.condition_met(ma20, twenty) is True
    below = _bars(*([39.00] * 19 + [38.04]))  # MA = (19×39 + 38.04)/20 = 38.952，收盘 38.04 未站上
    assert research.condition_met(ma20, below) is False
    assert research.condition_met(ma20, _bars(*([38.00] * 19))) is None


def test_research_check_trigger_expire_or_none():
    base = {
        "condition": {"kind": "close_above", "level": "38.00"},
        "created_session": date(2026, 11, 2),
        "valid_until": date(2026, 11, 30),
        "triggered_session": None,
    }
    # 当天缺日线 → 不判
    assert research.research_check(research=base, bar=None, bars=_bars(37.00), session=date(2026, 11, 30)) is None
    # 条件到了 → triggered，reason 逐字
    hit = research.research_check(
        research=base,
        bar={"close": D("38.50"), "low": D("37.80")},
        bars=_bars(37.00, 38.50),
        session=date(2026, 11, 30),
    )
    assert hit == {"event": "triggered", "reason": "收盘站上 38.00 已到：收盘 38.50"}
    # touch_below 的 reason 用「最低 …」（bar 与 bars 最后一根是同一天）
    touch_day = {
        "price_date": date(2026, 11, 30),
        "open": D("35.50"),
        "high": D("36.20"),
        "low": D("34.80"),
        "close": D("36.00"),
    }
    touch = research.research_check(
        research={**base, "condition": {"kind": "touch_below", "level": "35.00"}},
        bar=touch_day,
        bars=[touch_day],
        session=date(2026, 11, 30),
    )
    assert touch == {"event": "triggered", "reason": "最低价触及 35.00 或以下 已到：最低 34.80"}
    # 到期未到 → expired；n = 发起日的下一个交易日到当天的交易日数（2026-11-03…11-30 = 19）
    expired = research.research_check(
        research=base,
        bar={"close": D("37.50"), "low": D("37.00")},
        bars=_bars(37.50),
        session=date(2026, 11, 30),
    )
    assert expired == {"event": "expired", "reason": "19 个交易日条件未到"}
    # 有效期内未到 → None
    pending = research.research_check(
        research=base,
        bar={"close": D("37.50"), "low": D("37.00")},
        bars=_bars(37.50),
        session=date(2026, 11, 20),
    )
    assert pending is None
    # 已 triggered（待复看）→ 不再判触发、不过期
    waiting = {**base, "triggered_session": date(2026, 11, 20)}
    assert (
        research.research_check(
            research=waiting,
            bar={"close": D("38.50"), "low": D("37.80")},
            bars=_bars(38.50),
            session=date(2026, 11, 30),
        )
        is None
    )
