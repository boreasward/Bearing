"""The daily job (spec §5): one lock, the facts run, the brief row, the brief session and the
push. Steps 1–4 never depend on the model; a dead session leaves them intact (v3 设计验收 2)."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import investment_assistant.core.tx as core_tx
from investment_assistant.config import AssistantConfig, briefs_dir, load_config
from investment_assistant.daily import push
from investment_assistant.daily.session import run_brief_session
from investment_assistant.data import collect as data_collect
from investment_assistant.execution import retrospect
from investment_assistant.facts import plan_repo
from investment_assistant.facts import repo as facts_repo
from investment_assistant.facts import retrospect as facts_retrospect
from investment_assistant.facts.brief import render_brief, render_weekly
from investment_assistant.facts.job import run_facts
from investment_assistant.facts.view import brief_view
from investment_assistant.notify.discord import DiscordClient
from investment_assistant.runtime import lock, settings
from investment_assistant.runtime.base import Runtime, RuntimeSpec

# 错误简报第一行的判别串来自 facts.brief.render_error_brief（有持仓时才有这一行）
STOP_CHECK_WARNING = "今天无法检查止损"
_BEIJING = ZoneInfo("Asia/Shanghai")  # 周报页脚的时间口径（同简报）


def run_daily(
    *,
    now: datetime | None = None,
    collect: bool = True,
    cfg: AssistantConfig | None = None,
    spec: RuntimeSpec | None = None,
    runtime: Runtime | None = None,
    client=None,
    env_file: Path | None = None,
    budget_seconds: int | None = None,
) -> dict:
    """One day: acquire the brief lock, run the facts, then either the error brief, the
    no-model draft or the full session + final brief + push. ``budget_seconds`` overrides the
    config budget (the CLI never passes it; tests use it instead of burning the 20-minute
    default). Returns ``{'session','status','attempts','complete','pushed','brief_path'}``
    with status one of busy | error | draft | final."""
    cfg = cfg if cfg is not None else load_config()
    now = now if now is not None else data_collect.wall_clock()
    try:
        with lock.acquire("brief", cfg=cfg):
            return _run(
                now=now,
                collect=collect,
                cfg=cfg,
                spec=spec,
                runtime=runtime,
                client=client,
                env_file=env_file,
                budget_seconds=budget_seconds,
            )
    except lock.Busy:
        return {
            "session": None,
            "status": "busy",
            "attempts": 0,
            "complete": False,
            "pushed": False,
            "weekly_pushed": False,
            "brief_path": None,
        }


def _run(*, now, collect, cfg, spec, runtime, client, env_file, budget_seconds) -> dict:
    result = run_facts(now=now, collect=collect, cfg=cfg, trigger="scheduled")
    session = date.fromisoformat(result["session"])
    draft_md = Path(result["draft_path"]).read_text(encoding="utf-8")
    if result["status"] == "error":
        return _error_day(session=session, result=result, draft_md=draft_md, now=now, cfg=cfg, client=client)
    with core_tx.read_only_tx() as conn:
        spec = spec if spec is not None else settings.current(conn)
    if spec is None:
        return _no_model_day(session=session, result=result, draft_md=draft_md, now=now, cfg=cfg, client=client)
    return _session_day(
        session=session,
        result=result,
        draft_md=draft_md,
        now=now,
        cfg=cfg,
        spec=spec,
        runtime=runtime,
        client=client,
        env_file=env_file,
        budget_seconds=budget_seconds,
    )


def _error_day(*, session, result, draft_md, now, cfg, client) -> dict:
    """The facts failed (spec §5): persist the error brief, push the notice once."""
    client = client if client is not None else DiscordClient.from_config(cfg.notify)
    with core_tx._tx() as conn:
        row = plan_repo.upsert_brief(
            conn,
            _brief_row(
                session,
                run_id=result["run_id"],
                status="error",
                draft_md=draft_md,
                error=result["error"],
            ),
        )
    pushed = False
    if row["pushed_error_at"] is None:
        push.push_error(session, result["error"], STOP_CHECK_WARNING in draft_md, client, now=now)
        with core_tx._tx() as conn:
            plan_repo.update_brief(conn, session, pushed_error_at=now)
        pushed = True
    return {
        "session": session.isoformat(),
        "status": "error",
        "attempts": 0,
        "complete": False,
        "pushed": pushed,
        "weekly_pushed": False,  # 事实任务失败的日子不产出周报、下次运行不补（spec §5.4 / R5）
        "brief_path": result["draft_path"],
    }


def _weekly(session: date, *, now: datetime, cfg: AssistantConfig, client) -> bool:
    """周报（spec §5.4）：每周最后一个交易日的运行在最终简报之后算本周汇总、写进当天的
    简报行并单独推一条；同一周重跑不重推。全是宿主算的，模型不可用照常产出。"""
    if not retrospect.is_week_end(session):
        return False
    client = client if client is not None else DiscordClient.from_config(cfg.notify)
    with core_tx.read_only_tx() as conn:
        weekly = facts_retrospect.weekly_view(conn, week_end=session, cutoff_at=now, cfg=cfg)
    md = render_weekly(weekly, as_of=now.astimezone(_BEIJING))
    with core_tx._tx() as conn:
        plan_repo.update_brief(conn, session, weekly_md=md)
        row = plan_repo.brief(conn, session)
    assert row is not None  # 当天的简报行总是先写
    if row["pushed_weekly_at"] is None:
        push.push_weekly(md, client)
        with core_tx._tx() as conn:
            plan_repo.update_brief(conn, session, pushed_weekly_at=now)
        return True
    return False


def _no_model_day(*, session, result, draft_md, now, cfg, client=None) -> dict:
    """No model selected (spec §5): the brief stays a draft whose footer says so; no session,
    no brief push — the weekly report still goes out (B19)."""
    with core_tx._tx() as conn:
        plan_repo.upsert_brief(conn, _brief_row(session, run_id=result["run_id"], status="draft", draft_md=draft_md))
    path = _render_final(session, now=now, cfg=cfg)
    weekly_pushed = _weekly(session, now=now, cfg=cfg, client=client)
    return {
        "session": session.isoformat(),
        "status": "draft",
        "attempts": 0,
        "complete": False,
        "pushed": False,
        "weekly_pushed": weekly_pushed,
        "brief_path": str(path),
    }


def _session_day(*, session, result, draft_md, now, cfg, spec, runtime, client, env_file, budget_seconds) -> dict:
    """The full day: draft row, session, final brief, push once per trading session (A16, A22)."""
    client = client if client is not None else DiscordClient.from_config(cfg.notify)
    with core_tx._tx() as conn:
        row = plan_repo.upsert_brief(
            conn,
            _brief_row(
                session,
                run_id=result["run_id"],
                status="draft",
                draft_md=draft_md,
                runtime=spec.runtime,
                provider=spec.provider,
                model=spec.model,
            ),
        )
    rt = runtime if runtime is not None else settings.build(spec)
    budget = budget_seconds if budget_seconds is not None else cfg.v3.brief_budget_minutes * 60
    session_result = run_brief_session(
        session=session, cfg=cfg, runtime=rt, spec=spec, now=now, budget_seconds=budget, env_file=env_file
    )
    # 先记账再渲染：页脚的用量与耗时来自简报行（spec §9.4）
    with core_tx._tx() as conn:
        plan_repo.update_brief(
            conn,
            session,
            status="final",
            usage=session_result["usage"],
            elapsed_seconds=session_result["elapsed_seconds"],
            agenda=session_result["agenda"],
            error=session_result["error"],
        )
    with core_tx.read_only_tx() as conn:
        run_row = facts_repo.latest_run(conn, session)
        assert run_row is not None  # run_facts 刚写过这一天的运行记录
        view = brief_view(conn, session=session, cutoff_at=now, cfg=cfg, run=run_row)
    path = briefs_dir(cfg) / f"{session}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_brief(view, final=True), encoding="utf-8")
    with core_tx._tx() as conn:
        plan_repo.update_brief(conn, session, final_md=path.read_text(encoding="utf-8"))
    pushed = False
    if row["pushed_final_at"] is None:
        push.push_brief(view, brief_path=path, client=client)
        with core_tx._tx() as conn:
            plan_repo.update_brief(conn, session, pushed_final_at=now)
        pushed = True
    weekly_pushed = _weekly(session, now=now, cfg=cfg, client=client)
    return {
        "session": session.isoformat(),
        "status": "final",
        "attempts": session_result["attempts"],
        "complete": session_result["complete"],
        "pushed": pushed,
        "weekly_pushed": weekly_pushed,
        "brief_path": str(path),
    }


def _render_final(session: date, *, now, cfg) -> Path:
    """final_md without a session (spec §5 未选定模型)：footer comes from the brief row."""
    with core_tx.read_only_tx() as conn:
        run_row = facts_repo.latest_run(conn, session)
        assert run_row is not None  # run_facts 刚写过这一天的运行记录
        view = brief_view(conn, session=session, cutoff_at=now, cfg=cfg, run=run_row)
    path = briefs_dir(cfg) / f"{session}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_brief(view, final=True), encoding="utf-8")
    with core_tx._tx() as conn:
        plan_repo.update_brief(conn, session, final_md=path.read_text(encoding="utf-8"))
    return path


def _brief_row(
    session: date,
    *,
    run_id: str,
    status: str,
    draft_md: str,
    error: str | None = None,
    runtime: str | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> dict:
    """The full column set for upsert_brief — it NULLs whatever is not passed (carried finding 2)."""
    return {
        "session": session,
        "run_id": run_id,
        "status": status,
        "error": error,
        "draft_md": draft_md,
        "final_md": None,
        "summary": None,
        "runtime": runtime,
        "provider": provider,
        "model": model,
        "usage": {},
        "elapsed_seconds": None,
    }
