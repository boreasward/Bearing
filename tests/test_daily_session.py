"""简报会话（库测试）：第一次只完成部分待办就中断时重试只处理剩下的、已保存结论不变（A12）；
被杀掉/报错的会话不抛异常且原因入账（Review Focus 4）；预算耗尽返回未完成。"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from investment_assistant.runtime.base import RuntimeEvent, RuntimeSpec
from investment_assistant.runtime.fake import FakeRuntime

from investment_assistant.config import AssistantConfig, V3Config
from investment_assistant.data import repo as data_repo
from investment_assistant.data.calendar import add_trading_days
from investment_assistant.facts import plan_repo
from investment_assistant.notify.discord import DiscordChannel
from investment_assistant.toolsurface.env import make_env

NOW = datetime(2026, 11, 5, 3, 30, tzinfo=UTC)
SESSION = date(2026, 11, 4)
SPEC = RuntimeSpec(runtime="fake", provider="prov-x", model="model-x")
SEEN_AT = NOW - timedelta(hours=1)
STOCKS = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "INTC")
BREAKOUTS = ("AAPL", "MSFT")


def cfg_at(tmp_path, **v3_kwargs):
    return AssistantConfig(v3=V3Config(briefs_dir=str(tmp_path / "rb" / "briefs"), **v3_kwargs))


def seed_bars(conn, tickers, *, breakout=(), low_override=None):
    for ticker in tickers:
        for i in range(300):
            close, volume = (
                (110.0, 3_000_000) if i == 299 and ticker in breakout else (100.0 + (i % 5) * 0.05, 1_000_000)
            )
            low = round(close * 0.99, 2)
            if low_override and i == 299 and ticker in low_override:
                low = low_override[ticker]
            conn.execute(
                "INSERT INTO price_bars (ticker, price_date, open, high, low, close, adj_close, volume, "
                "source, revision, fetched_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'yfinance', 1, %s)",
                (ticker, add_trading_days(SESSION, -(299 - i)), close, close, low, close, close, volume, SEEN_AT),
            )


def market_ticklers(cfg):
    return tuple(dict.fromkeys(("SPY", "QQQ", *cfg.v3.sector_etfs, *STOCKS)))


class Recorder:
    def __init__(self):
        self.sent = []

    def send_text(self, channel, text):
        self.sent.append((channel, text))


def open_plan(conn, ticker, *, plan_session=date(2026, 11, 3)):
    return plan_repo.insert_plan(
        conn,
        {
            "session": plan_session,
            "ticker": ticker,
            "origin": "brief",
            "entry_type": "zone_limit",
            "entry_low": Decimal(105),
            "entry_high": Decimal(110),
            "stop": Decimal(100),
            "target1": Decimal(130),
            "initial_stop": Decimal(100),
            "atr": Decimal(2),
            "levels_source": "host",
            "max_hold_sessions": 20,
            "valid_from": date(2026, 11, 4),
            "valid_until": date(2026, 11, 10),
            "shares": 10,
            "sizing": {},
            "warnings": [],
            "thesis": "",
            "invalidation": "",
            "agent_session": "session-test",
            "status": "open",
            "kind": "planned",
            "start_session": plan_session,
            "reviewed_through": plan_session,
        },
    )


def _open_plans(v3_tx):
    with v3_tx.cursor() as cur:
        cur.execute("SELECT id FROM v3_plans WHERE status = 'open' ORDER BY ticker")
        return [row[0] for row in cur.fetchall()]


def test_partial_failure_retries_only_the_remaining_items(v3_tx, tmp_path):
    """A12：第一次只保存了 AAPL 的复查就 error；第二次只碰剩下的 MSFT，AAPL 的结论不变。"""
    from investment_assistant.daily.session import run_brief_session

    import investment_assistant.core.tx as core_tx

    cfg = cfg_at(tmp_path)
    with core_tx._tx() as conn:
        seed_bars(conn, market_ticklers(cfg), breakout=BREAKOUTS)
        first = open_plan(conn, "AAPL")
        second = open_plan(conn, "MSFT")
    v3_tx.commit()

    attempt = {"n": 0}

    def script(prompt: str):
        attempt["n"] += 1
        if attempt["n"] == 1:  # 只完成第一项就中断
            return [
                ("call", "save_holding_review", {"plan_id": first["id"], "verdict": "holds", "reasons": "论点仍成立"}),
                RuntimeEvent("error", text="模型中断"),
            ]
        return [
            ("call", "save_holding_review", {"plan_id": second["id"], "verdict": "unknown", "reasons": "数据不足"}),
            ("call", "save_summary", {"text": "今天两只持仓：一只成立，一只无法判断。"}),
            RuntimeEvent("done"),
        ]

    counter = {"n": 0}

    def env_factory():
        counter["n"] += 1
        return make_env(session=SESSION, mode="brief", agent_session=f"brief-{SESSION.isoformat()}-{counter['n']}")

    fake = FakeRuntime(script, env=env_factory)
    result = run_brief_session(session=SESSION, cfg=cfg, runtime=fake, spec=SPEC, now=NOW, budget_seconds=120)
    assert result["attempts"] == 2 and result["complete"] is True and result["error"] is None
    assert result["elapsed_seconds"] >= 0 and result["usage"] == {"input": 0, "output": 0, "total": 0}
    with core_tx.read_only_tx() as conn:
        reviews = plan_repo.holding_reviews(conn, SESSION)
    assert set(reviews) == {first["id"], second["id"]}
    assert reviews[first["id"]]["verdict"] == "holds"  # 第一次的结论没被重做
    assert reviews[first["id"]]["reasons"] == "论点仍成立"
    assert reviews[second["id"]]["verdict"] == "unknown"
    calls = v3_tx.execute("SELECT agent_session, tool FROM v3_tool_calls ORDER BY id").fetchall()
    assert [(row[0], row[1]) for row in calls] == [
        (f"brief-{SESSION.isoformat()}-1", "save_holding_review"),  # 第一次只处理 AAPL
        (f"brief-{SESSION.isoformat()}-2", "save_holding_review"),  # 第二次只剩 MSFT 与总结
        (f"brief-{SESSION.isoformat()}-2", "save_summary"),
    ]
    assert sorted(result["agenda"]["holdings"], key=lambda item: item["ticker"]) == [
        {"plan_id": first["id"], "ticker": "AAPL", "done": True},
        {"plan_id": second["id"], "ticker": "MSFT", "done": True},
    ]


def test_killed_session_keeps_draft_and_records_reason(v3_tx, tmp_path):
    """Review Focus 4：会话无输出被超时杀掉——简报行状态与未完成原因写明，草稿与宿主算的持仓
    操作不受影响，不抛异常。"""
    from investment_assistant.daily.job import run_daily

    import investment_assistant.core.tx as core_tx
    from investment_assistant.facts import repo as facts_repo

    cfg = cfg_at(tmp_path)
    with core_tx._tx() as conn:
        seed_bars(conn, market_ticklers(cfg), breakout=BREAKOUTS)
        open_plan(conn, "AAPL")
        data_repo.store_bars(
            conn,
            "AAPL",
            [
                {
                    "price_date": SESSION,
                    "open": 110.0,
                    "high": 110.5,
                    "low": 95.0,
                    "close": 110.0,
                    "adj_close": 110.0,
                    "volume": 3_000_000,
                    "split_factor": 1.0,
                }
            ],
            now=SEEN_AT,
        )
        conn.execute(
            "INSERT INTO v3_trades (request_id, ticker, side, quantity, price, executed_at, note) "
            "VALUES ('buy-1', 'AAPL', 'buy', 10, 106, %s, '')",
            (datetime(2026, 11, 3, 16, tzinfo=UTC),),
        )
    v3_tx.commit()

    recorder = Recorder()
    runtime = FakeRuntime([RuntimeEvent("text", text=f"第{i}段") for i in range(80)], delay=0.05)
    result = run_daily(
        now=NOW, collect=False, cfg=cfg, spec=SPEC, runtime=runtime, client=recorder, budget_seconds=2
    )

    assert result["status"] == "final" and result["complete"] is False and result["attempts"] == 1
    with core_tx.read_only_tx() as conn:
        row = plan_repo.brief(conn, SESSION)
        run_row = facts_repo.latest_run(conn, SESSION)
    assert row is not None and run_row is not None
    assert row["status"] == "final" and row["error"] == "timeout"
    assert "## 今日操作" in row["draft_md"] and "止损已触及" in row["draft_md"]  # 草稿不受影响
    assert run_row["draft_md"] == row["draft_md"]
    with open(result["brief_path"], encoding="utf-8") as fh:
        final_md = fh.read()
    assert "## 今日操作" in final_md and "AAPL：止损已触及，未见卖出记录" in final_md
    assert "- 未完成：持仓复查 AAPL" in final_md
    assert [channel for channel, _ in recorder.sent] == [DiscordChannel.SIGNALS]  # 宿主部分照常推送


def test_timeout_killed_session_reports_timeout(v3_tx, tmp_path):
    """会话无输出被超时杀掉（Review Focus 4 的另一半）：error 记 'timeout'，不抛异常。"""
    from investment_assistant.daily.session import run_brief_session

    import investment_assistant.core.tx as core_tx

    cfg = cfg_at(tmp_path)
    with core_tx._tx() as conn:
        seed_bars(conn, ["SPY"])  # 议程只会缺一句话总结，无需完整数据
    v3_tx.commit()
    runtime = FakeRuntime([RuntimeEvent("text", text=f"第{i}段") for i in range(40)], delay=0.05)
    result = run_brief_session(session=SESSION, cfg=cfg, runtime=runtime, spec=SPEC, now=NOW, budget_seconds=1)
    assert result["attempts"] == 1 and result["complete"] is False
    assert result["error"] == "timeout"


def test_budget_exhaustion_reports_incomplete(v3_tx, tmp_path):
    from investment_assistant.daily.session import run_brief_session

    cfg = cfg_at(tmp_path)
    runtime = FakeRuntime([RuntimeEvent("done")])  # 预算为零，一次都不启动
    result = run_brief_session(session=SESSION, cfg=cfg, runtime=runtime, spec=SPEC, now=NOW, budget_seconds=0)
    assert result["attempts"] == 0 and result["complete"] is False
    assert result["error"] and "预算" in result["error"]
    assert result["agenda"]["summary_done"] is False


def test_pi_unavailable_records_reason_without_raising(v3_tx, tmp_path):
    """v3 设计验收 2：pi 不可用（连进程都起不来）——不抛异常，原因入账，待办判未完成。"""
    from investment_assistant.daily.session import run_brief_session
    from investment_assistant.runtime.pi import PiRuntime

    cfg = cfg_at(tmp_path)
    runtime = PiRuntime(binary=tmp_path / "no-such-pi")
    result = run_brief_session(session=SESSION, cfg=cfg, runtime=runtime, spec=SPEC, now=NOW, budget_seconds=5)
    assert result["attempts"] == 1 and result["complete"] is False
    assert "no-such-pi" in result["error"]
