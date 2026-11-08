"""每日任务（库测试，真实 run_facts、collect=False）：错误日推送一次且首行「今天无法检查止损」；
同交易日重跑不重复推送（A16）；先错后成再推一次、第三次不推（A22）；未选模型不推送；busy 退出码 3；
runtime 报错时最终简报仍含宿主部分与「未完成」（A17）。"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from investment_assistant.config import AssistantConfig, V3Config
from investment_assistant.daily.__main__ import EXIT_CODES
from investment_assistant.data.calendar import add_trading_days
from investment_assistant.facts import plan_repo
from investment_assistant.notify.discord import DiscordChannel
from investment_assistant.runtime.base import RuntimeEvent, RuntimeSpec
from investment_assistant.runtime.fake import FakeRuntime
from investment_assistant.toolsurface.env import make_env

NOW = datetime(2026, 11, 5, 3, 30, tzinfo=UTC)
SESSION = date(2026, 11, 4)
FRIDAY = date(2026, 11, 6)  # 本周最后一个交易日（下一个交易日 11/9 在另一 ISO 周）
NOW_FRIDAY = datetime(2026, 11, 7, 3, 30, tzinfo=UTC)
SPEC = RuntimeSpec(runtime="fake", provider="prov-x", model="model-x")
SEEN_AT = NOW - timedelta(hours=1)
STOCKS = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "INTC")
BREAKOUTS = ("AAPL", "MSFT")


def cfg_at(tmp_path, **v3_kwargs):
    return AssistantConfig(v3=V3Config(briefs_dir=str(tmp_path / "rb" / "briefs"), **v3_kwargs))


def seed_bars(conn, tickers, *, breakout=(), end=SESSION):
    for ticker in tickers:
        for i in range(300):
            close, volume = (
                (110.0, 3_000_000) if i == 299 and ticker in breakout else (100.0 + (i % 5) * 0.05, 1_000_000)
            )
            conn.execute(
                "INSERT INTO price_bars (ticker, price_date, open, high, low, close, adj_close, volume, "
                "source, revision, fetched_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'yfinance', 1, %s)",
                (
                    ticker,
                    add_trading_days(end, -(299 - i)),
                    close,
                    close,
                    round(close * 0.99, 2),
                    close,
                    close,
                    volume,
                    SEEN_AT,
                ),
            )


def market_ticklers(cfg):
    return tuple(dict.fromkeys(("SPY", "QQQ", *cfg.v3.sector_etfs, *STOCKS)))


class Recorder:
    def __init__(self):
        self.sent = []

    def send_text(self, channel, text):
        self.sent.append((channel, text))


def completing_runtime(session=SESSION):
    """一轮把当天待办做完：两只候选各存 unknown 结论 + 一句话总结。"""

    def script(prompt: str):
        return [
            ("call", "save_verdict", {"ticker": "AAPL", "verdict": "unknown", "reasons": "数据不足"}),
            ("call", "save_verdict", {"ticker": "MSFT", "verdict": "unknown", "reasons": "数据不足"}),
            ("call", "save_summary", {"text": "今天两只都无法判断，未出新计划。"}),
            RuntimeEvent("done"),
        ]

    return FakeRuntime(
        script, env=lambda: make_env(session=session, mode="brief", agent_session=f"brief-{session.isoformat()}-1")
    )


def seed_holding(conn):
    """AAPL 已买入持仓：SESSION 低点穿止损，草稿/简报里有「今日操作」。"""
    from investment_assistant.data import repo as data_repo
    from investment_assistant.facts import repo as facts_repo

    plan_repo.insert_plan(
        conn,
        {
            "session": date(2026, 11, 3),
            "ticker": "AAPL",
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
            "agent_session": "job-test",
            "status": "open",
            "kind": "planned",
            "start_session": date(2026, 11, 3),
            "reviewed_through": date(2026, 11, 3),
        },
    )
    facts_repo.insert_trade(
        conn,
        {
            "request_id": "buy-1",
            "ticker": "AAPL",
            "side": "buy",
            "quantity": 10,
            "price": Decimal(106),
            "executed_at": datetime(2026, 11, 3, 16, tzinfo=UTC),
        },
    )
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


def test_error_day_pushes_once_with_stop_warning_line(v3_tx, tmp_path):
    import investment_assistant.core.tx as core_tx

    cfg = cfg_at(tmp_path)
    with core_tx._tx() as conn:
        seed_bars(conn, [t for t in market_ticklers(cfg) if t != "SPY"], breakout=BREAKOUTS)
        seed_holding(conn)
    v3_tx.commit()
    client = Recorder()
    result = run_daily_with(cfg, client)
    assert result["status"] == "error" and EXIT_CODES["error"] == 1
    assert client.sent and client.sent[0][0] == DiscordChannel.SIGNALS
    first_line = client.sent[0][1].splitlines()[0]
    assert first_line.startswith("今天无法检查止损")
    with core_tx.read_only_tx() as conn:
        row = plan_repo.brief(conn, SESSION)
    assert row is not None and row["status"] == "error" and row["pushed_error_at"] is not None


def run_daily_with(cfg, client, **overrides):
    from investment_assistant.daily.job import run_daily

    kwargs = {"now": NOW, "collect": False, "cfg": cfg, "spec": SPEC, "client": client}
    kwargs.update(overrides)
    return run_daily(**kwargs)


def test_same_session_rerun_does_not_repush(v3_tx, tmp_path):
    import investment_assistant.core.tx as core_tx

    cfg = cfg_at(tmp_path)
    with core_tx._tx() as conn:
        seed_bars(conn, market_ticklers(cfg), breakout=BREAKOUTS)
    v3_tx.commit()
    client = Recorder()
    first = run_daily_with(cfg, client, runtime=completing_runtime(), budget_seconds=60)
    assert first["status"] == "final" and first["complete"] is True and first["pushed"] is True
    second = run_daily_with(cfg, client, runtime=completing_runtime(), budget_seconds=60)
    assert second["status"] == "final" and second["pushed"] is False  # A16：不重复推送
    assert len(client.sent) == 1
    assert "简报文件" in client.sent[0][1]
    with core_tx.read_only_tx() as conn:
        row = plan_repo.brief(conn, SESSION)
    assert row is not None and row["pushed_final_at"] is not None and row["status"] == "final"


def test_error_then_success_pushes_again_then_stops(v3_tx, tmp_path):
    import investment_assistant.core.tx as core_tx

    cfg = cfg_at(tmp_path)
    with core_tx._tx() as conn:
        seed_bars(conn, [t for t in market_ticklers(cfg) if t != "SPY"], breakout=BREAKOUTS)
    v3_tx.commit()
    client = Recorder()
    assert run_daily_with(cfg, client)["status"] == "error"
    seed_bars(v3_tx, ["SPY"])
    v3_tx.commit()
    ok = run_daily_with(cfg, client, runtime=completing_runtime(), budget_seconds=60)
    assert ok["status"] == "final" and ok["pushed"] is True  # A22：错后再成要再推
    again = run_daily_with(cfg, client, runtime=completing_runtime(), budget_seconds=60)
    assert again["pushed"] is False
    assert len(client.sent) == 2  # 一次错误通知 + 一次成功简报，第三次不推
    assert client.sent[0][1].startswith("今天没有简报：基准日线缺失")
    assert "简报文件" in client.sent[1][1]


def test_no_model_keeps_draft_note_and_skips_push(v3_tx, tmp_path):
    import investment_assistant.core.tx as core_tx

    cfg = cfg_at(tmp_path)
    with core_tx._tx() as conn:
        seed_bars(conn, market_ticklers(cfg), breakout=BREAKOUTS)
    v3_tx.commit()
    client = Recorder()
    result = run_daily(cfg=cfg, spec=None, client=client)
    assert result["status"] == "draft" and result["pushed"] is False and not client.sent
    with open(result["brief_path"], encoding="utf-8") as fh:
        final_md = fh.read()
    assert "未选定模型，二次确认未做" in final_md
    with core_tx.read_only_tx() as conn:
        row = plan_repo.brief(conn, SESSION)
    assert row is not None and row["status"] == "draft" and row["final_md"] == final_md


def run_daily(**kwargs):
    from investment_assistant.daily.job import run_daily as _run

    kwargs.setdefault("now", NOW)
    kwargs.setdefault("collect", False)
    return _run(**kwargs)


def test_busy_returns_busy_and_exit_code_3(v3_tx, tmp_path):
    from investment_assistant.runtime import lock

    cfg = cfg_at(tmp_path)
    with lock.acquire("brief", cfg=cfg):
        result = run_daily(cfg=cfg, spec=SPEC, client=Recorder())
    assert result["status"] == "busy" and EXIT_CODES["busy"] == 3


def test_runtime_error_final_brief_keeps_host_parts(v3_tx, tmp_path):
    """A17：模型吐 error——草稿与最终简报的宿主部分齐全，未完成项写明，退出码仍为 0。"""
    import investment_assistant.core.tx as core_tx

    cfg = cfg_at(tmp_path)
    with core_tx._tx() as conn:
        seed_bars(conn, market_ticklers(cfg), breakout=BREAKOUTS)
        seed_holding(conn)
    v3_tx.commit()
    client = Recorder()
    attempt = {"n": 0}

    def script(prompt: str):
        attempt["n"] += 1
        if attempt["n"] == 1:
            return [RuntimeEvent("text", text="开始"), RuntimeEvent("error", text="模型返回错误")]
        return [RuntimeEvent("text", text=f"第{i}段") for i in range(80)]  # 第二次挂起直到预算耗尽

    runtime = FakeRuntime(script, delay=0.05)
    result = run_daily_with(cfg, client, runtime=runtime, budget_seconds=3)
    assert result["status"] == "final" and result["complete"] is False and result["attempts"] == 2
    with open(result["brief_path"], encoding="utf-8") as fh:
        final_md = fh.read()
    assert "## 今日操作" in final_md and "AAPL：止损已触及，未见卖出记录" in final_md
    assert "- 未完成：持仓复查 AAPL" in final_md
    assert len(client.sent) == 1 and "止损已触及" in client.sent[0][1]
    with core_tx.read_only_tx() as conn:
        row = plan_repo.brief(conn, SESSION)
    assert row is not None and row["error"] and row["usage"] == {"input": 0, "output": 0, "total": 0}


def test_cli_argument_errors_exit_2(capsys):
    from investment_assistant.daily.__main__ import main

    assert main(["run", "--provider", "prov-x"]) == 2  # 临时指定必须三项齐全
    assert "同时给出" in capsys.readouterr().err


def test_plist_template_placeholders():
    template = (
        Path(__file__).parents[1] / "scripts" / "launchd" / "local.investment-assistant.daily.plist.example"
    ).read_text(encoding="utf-8")
    assert "__REPO__/.venv/bin/python" in template
    assert "--env-file" in template and "__REPO__/.env" in template
    assert template.count("<key>Weekday</key>") == 5  # 周二至周六
    assert "__HOME__/.pi-ia" in template and "INVESTMENT_ASSISTANT_RUNTIME_BASE" in template
    assert "daily.stdout.log" in template and "daily.stderr.log" in template
    # 与页面服务（.env、local-service.md）同一个运行目录：两边共用 pi.lock，简报落在同一个 briefs/
    assert "__HOME__/Library/Application Support/investment-assistant</string>" in template
    assert ".local/v3" not in template
    # 包从仓库根目录导入（未装进 venv），launchd 缺省工作目录是 /
    assert "<key>WorkingDirectory</key>\n    <string>__REPO__</string>" in template


def test_week_end_writes_and_pushes_weekly_once(v3_tx, tmp_path):
    """B6：周五 session 产出 weekly_md 并多推一条 Discord；同一周重跑不重推。"""
    import investment_assistant.core.tx as core_tx

    cfg = cfg_at(tmp_path)
    with core_tx._tx() as conn:
        seed_bars(conn, market_ticklers(cfg), breakout=BREAKOUTS, end=FRIDAY)
    v3_tx.commit()
    client = Recorder()
    first = run_daily_with(
        cfg, client, now=NOW_FRIDAY, runtime=completing_runtime(FRIDAY), budget_seconds=60
    )
    assert first["status"] == "final" and first["pushed"] is True and first["weekly_pushed"] is True
    with core_tx.read_only_tx() as conn:
        row = plan_repo.brief(conn, FRIDAY)
    assert row is not None and "# 周报" in row["weekly_md"]
    assert row["pushed_weekly_at"] is not None
    assert len(client.sent) == 2 and "# 周报" in client.sent[1][1]
    second = run_daily_with(
        cfg, client, now=NOW_FRIDAY, runtime=completing_runtime(FRIDAY), budget_seconds=60
    )
    assert second["pushed"] is False and second["weekly_pushed"] is False
    assert len(client.sent) == 2  # 重跑不重推


def test_no_model_week_end_still_pushes_weekly(v3_tx, tmp_path):
    """B19：没选定模型的周末日仍产出并推送周报（全是宿主算的），简报本身不推。"""
    import investment_assistant.core.tx as core_tx

    cfg = cfg_at(tmp_path)
    seed_bars(v3_tx, market_ticklers(cfg), end=FRIDAY)
    v3_tx.commit()
    client = Recorder()
    result = run_daily(cfg=cfg, spec=None, client=client, now=NOW_FRIDAY)
    assert result["status"] == "draft" and result["pushed"] is False and result["weekly_pushed"] is True
    with core_tx.read_only_tx() as conn:
        row = plan_repo.brief(conn, FRIDAY)
    assert row is not None and "# 周报" in row["weekly_md"] and row["pushed_weekly_at"] is not None
    assert len(client.sent) == 1 and "# 周报" in client.sent[0][1]


def test_midweek_has_no_weekly(v3_tx, tmp_path):
    """周三（非周末交易日）不产出周报，也不多推。"""
    import investment_assistant.core.tx as core_tx

    cfg = cfg_at(tmp_path)
    seed_bars(v3_tx, market_ticklers(cfg), breakout=BREAKOUTS)
    v3_tx.commit()
    client = Recorder()
    result = run_daily_with(cfg, client, runtime=completing_runtime(), budget_seconds=60)
    assert result["status"] == "final" and result["weekly_pushed"] is False
    with core_tx.read_only_tx() as conn:
        row = plan_repo.brief(conn, SESSION)
    assert row is not None and row["weekly_md"] is None and row["pushed_weekly_at"] is None
    assert len(client.sent) == 1
