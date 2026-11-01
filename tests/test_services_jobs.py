import pytest

from investment_assistant.services import jobs


def test_scheduled_jobs_degraded_without_db(monkeypatch):
    monkeypatch.delenv("INVESTMENT_ASSISTANT_DATABASE_URL", raising=False)
    out = jobs.scheduled_jobs()
    assert out["degraded"] is True and out["jobs"] == []
    assert out["scheduler"]["meta"]["enabled"] is False


def test_scheduled_jobs_exposes_global_disable_despite_enabled_job(client, monkeypatch):
    monkeypatch.setenv("INVESTMENT_ASSISTANT_DATABASE_URL", "postgres://x")
    monkeypatch.setenv("SCHEDULER_ENABLED", "0")
    monkeypatch.setattr(jobs, "_with_conn", lambda fn: fn("CONN"))
    monkeypatch.setattr(jobs.db, "list_scheduled_jobs",
                        lambda conn: [{"name": "metrics", "enabled": True}])
    response = client.get("/api/jobs/scheduled")
    assert response.status_code == 200
    out = response.json()
    assert out["jobs"][0]["enabled"] is True
    assert out["degraded"] is False  # 数据库可用与调度开关独立
    assert out["scheduler"]["meta"]["enabled"] is False
    assert "SCHEDULER_ENABLED=0" in out["scheduler"]["detail"]


def test_job_reports_reads_via_db(monkeypatch):
    monkeypatch.setenv("INVESTMENT_ASSISTANT_DATABASE_URL", "postgres://x")
    monkeypatch.setattr(jobs, "_with_conn", lambda fn: fn("CONN"))
    monkeypatch.setattr(jobs.db, "list_job_reports",
                        lambda conn, *, task, limit: [{"task": task, "run_id": "r1"}])
    out = jobs.job_reports(task="metrics", limit=5)
    assert out["degraded"] is False and out["reports"][0]["run_id"] == "r1"


def test_trigger_job_unknown_raises(monkeypatch):
    monkeypatch.setattr(jobs, "REGISTRY", {})
    with pytest.raises(ValueError):
        jobs.trigger_job("ghost")


def test_trigger_job_submits(monkeypatch):
    captured = {}
    monkeypatch.setattr(jobs, "REGISTRY", {"metrics": lambda config, **kw: {"ok": True}})
    monkeypatch.setattr(jobs, "load_config", lambda: object())
    monkeypatch.setattr(jobs.runner, "submit", lambda kind, fn: captured.update({"kind": kind}) or "run-1")
    out = jobs.trigger_job("metrics")
    assert out["run_id"] == "run-1" and out["status"] == "pending" and captured["kind"] == "metrics"


def test_trigger_job_filings_passes_date_range(monkeypatch):
    from datetime import date

    seen = {}
    monkeypatch.setattr(jobs, "REGISTRY", {"filings": lambda config, **kw: seen.update(kw) or {"ok": True}})
    monkeypatch.setattr(jobs, "load_config", lambda: object())
    submitted = {}
    monkeypatch.setattr(jobs.runner, "submit", lambda kind, fn: submitted.update({"fn": fn}) or "run-2")

    out = jobs.trigger_job("filings", {"from": "2026-06-01", "to": "2026-06-30"})
    submitted["fn"]()  # 同步执行以观察参数
    assert out["run_id"] == "run-2"
    assert seen == {"start_date": date(2026, 6, 1), "end_date": date(2026, 6, 30),
                    "trigger_source": "manual"}


def test_trigger_job_filings_single_date(monkeypatch):
    from datetime import date

    seen = {}
    monkeypatch.setattr(jobs, "REGISTRY", {"filings": lambda config, **kw: seen.update(kw) or {"ok": True}})
    monkeypatch.setattr(jobs, "load_config", lambda: object())
    submitted = {}
    monkeypatch.setattr(jobs.runner, "submit", lambda kind, fn: submitted.update({"fn": fn}) or "run-3")

    jobs.trigger_job("filings", {"date": "2026-06-15"})
    submitted["fn"]()
    assert seen == {"start_date": date(2026, 6, 15), "end_date": date(2026, 6, 15),
                    "trigger_source": "manual"}


def test_trigger_job_filings_rejects_bad_range(monkeypatch):
    monkeypatch.setattr(jobs, "REGISTRY", {"filings": lambda config, **kw: {"ok": True}})
    monkeypatch.setattr(jobs, "load_config", lambda: object())
    with pytest.raises(ValueError):
        jobs.trigger_job("filings", {"from": "2026-06-30", "to": "2026-06-01"})
    with pytest.raises(ValueError):
        jobs.trigger_job("filings", {"from": "2025-01-01", "to": "2026-01-01"})  # 超过范围上限


def test_trigger_job_unknown_is_distinct_error(monkeypatch):
    monkeypatch.setattr(jobs, "REGISTRY", {})
    with pytest.raises(jobs.UnknownJobError):
        jobs.trigger_job("ghost")


def test_patch_scheduled_job_passthrough(monkeypatch):
    monkeypatch.setenv("INVESTMENT_ASSISTANT_DATABASE_URL", "postgres://x")
    seen = {}
    monkeypatch.setattr(jobs, "_with_conn", lambda fn: fn("CONN"))
    monkeypatch.setattr(jobs.db, "update_scheduled_job",
                        lambda conn, name, **kw: seen.update({"name": name, **kw}))
    out = jobs.patch_scheduled_job("metrics", time_local="09:30")
    assert out["updated"] is True and seen == {"name": "metrics", "enabled": None, "time_local": "09:30"}


def test_trigger_job_metrics_accepts_dates(monkeypatch):
    from datetime import date

    seen = {}
    monkeypatch.setattr(jobs, "REGISTRY", {"metrics": lambda config, **kw: seen.update(kw) or {}})
    monkeypatch.setattr(jobs, "load_config", lambda: object())
    submitted = {}
    monkeypatch.setattr(jobs.runner, "submit", lambda kind, fn: submitted.update({"fn": fn}) or "run-9")
    jobs.trigger_job("metrics", {"date": "2026-06-15"})
    submitted["fn"]()
    assert seen == {"start_date": date(2026, 6, 15), "end_date": date(2026, 6, 15),
                    "trigger_source": "manual"}


def test_trigger_job_rejects_future_date(monkeypatch):
    from datetime import date, timedelta

    monkeypatch.setattr(jobs, "REGISTRY", {"metrics": lambda config, **kw: {}})
    monkeypatch.setattr(jobs, "load_config", lambda: object())
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError):
        jobs.trigger_job("metrics", {"date": tomorrow})


def test_scheduled_jobs_carry_description(monkeypatch):
    from investment_assistant.tasks import catalog

    monkeypatch.setattr(jobs, "_has_db", lambda: True)
    monkeypatch.setattr(jobs, "_with_conn", lambda fn: [
        {"name": "daily", "enabled": True},
        {"name": "ghost", "enabled": True},
    ])
    out = jobs.scheduled_jobs()
    assert out["degraded"] is False
    assert out["jobs"][0]["description"]["title"] == catalog.JOB_INFO["daily"]["title"]
    assert out["jobs"][1]["description"] is None


def test_scheduled_jobs_list_manual_only_registry_jobs(monkeypatch):
    """``sec_backfill`` has no scheduled_jobs row; the task center still gets a row for it (only 按日期运行)."""
    from investment_assistant.tasks import catalog

    monkeypatch.setattr(jobs, "_has_db", lambda: True)
    monkeypatch.setattr(jobs, "_with_conn", lambda fn: [{"name": "daily", "enabled": True}])
    out = jobs.scheduled_jobs()["jobs"]
    assert out[0] == {"name": "daily", "enabled": True, "description": catalog.describe("daily")}   # normal rows as before
    assert out[1] == {"name": "sec_backfill", "manual_only": True, "enabled": False, "next_run_at": None,
                      "time_local": None, "weekday_mask": None, "timezone": None,
                      "description": catalog.describe("sec_backfill")}
    assert len(out) == 2
    monkeypatch.setattr(jobs, "_with_conn", lambda fn: [{"name": "sec_backfill", "enabled": True}])    # a real row wins
    assert [j.get("manual_only") for j in jobs.scheduled_jobs()["jobs"]] == [None]


def test_old_job_names_404(client, monkeypatch):
    monkeypatch.setattr(jobs, "load_config", lambda: object())
    for old in ("workbench", "workbench_review", "filings"):
        with pytest.raises(jobs.UnknownJobError):
            jobs.trigger_job(old)
        response = client.post(f"/api/jobs/{old}/run", json={})
        assert response.status_code == 404 and response.json() == {"error": f"unknown job: {old}"}


def test_sec_backfill_requires_dates(client, monkeypatch):
    monkeypatch.setattr(jobs, "load_config", lambda: object())
    submitted = {}
    monkeypatch.setattr(jobs.runner, "submit", lambda kind, fn: submitted.update({"kind": kind, "fn": fn}) or "run-9")
    with pytest.raises(ValueError):
        jobs.trigger_job("sec_backfill", {})
    assert client.post("/api/jobs/sec_backfill/run", json={}).status_code == 400
    assert submitted == {}
    out = jobs.trigger_job("sec_backfill", {"date": "2026-10-01"})
    assert out == {"run_id": "run-9", "status": "pending"} and submitted["kind"] == "sec_backfill"


def test_daily_job_ignores_dates_and_returns_its_run_id(monkeypatch):
    from investment_assistant.core import locks

    seen = {}
    monkeypatch.setattr(jobs, "REGISTRY", {"daily": lambda config, **kw: seen.update(kw) or {"ok": True}})
    monkeypatch.setattr(jobs, "load_config", lambda: object())
    monkeypatch.setattr(locks, "is_running", lambda: False)
    monkeypatch.setattr(jobs.runner, "submit", lambda kind, fn: fn() and "runner-3")
    out = jobs.trigger_job("daily", {"date": "2026-10-01"})
    assert out["run_id"] == "runner-3" and out["status"] == "pending"
    assert out["daily_run_id"] == seen["run_id"]                   # the id the run will use (job_reports / detail page)
    assert out["daily_run_id"].startswith("daily-") and set(seen) == {"trigger_source", "run_id"}
    assert seen["trigger_source"] == "manual"


@pytest.mark.parametrize("name, payload", [("daily", {}), ("sec_backfill", {"date": "2026-10-01"})])
def test_manual_runs_raise_daily_busy_while_the_lock_is_held(monkeypatch, name, payload):
    from investment_assistant.core import locks

    monkeypatch.setattr(jobs, "REGISTRY", {name: lambda config, **kw: pytest.fail("must not run")})
    monkeypatch.setattr(jobs, "load_config", lambda: object())
    monkeypatch.setattr(locks, "is_running", lambda: True)
    monkeypatch.setattr(jobs.runner, "submit", lambda *a, **k: pytest.fail("must not submit"))
    with pytest.raises(locks.DailyBusy):
        jobs.trigger_job(name, payload)


def test_a_missing_database_does_not_block_the_manual_run(monkeypatch):
    from investment_assistant.core import locks

    def no_db():
        raise RuntimeError("数据库未配置")

    monkeypatch.setattr(jobs, "REGISTRY", {"daily": lambda config, **kw: {"ok": True}})
    monkeypatch.setattr(jobs, "load_config", lambda: object())
    monkeypatch.setattr(locks, "is_running", no_db)
    monkeypatch.setattr(jobs.runner, "submit", lambda kind, fn: "runner-4")
    assert jobs.trigger_job("daily")["run_id"] == "runner-4"


def test_resume_daily_checks_then_submits_in_that_order(monkeypatch):
    from investment_assistant.tasks import daily
    from investment_assistant.core import locks
    import investment_assistant.workflow.graph as workflow

    order = []
    run_id = "daily-20261030033000-1a2b3c4d"
    monkeypatch.setattr(workflow, "resume_check", lambda rid, node, *, now: order.append("check") or {"attempt": 3})
    monkeypatch.setattr(daily, "ensure_ready", lambda: order.append("ready"))
    monkeypatch.setattr(locks, "is_running", lambda: order.append("lock") or False)
    monkeypatch.setattr(jobs.runner, "submit", lambda kind, fn: order.append(("submit", kind)) or "daily_resume-x")
    out = jobs.resume_daily({"run_id": run_id, "node": "collect", "request_id": "ignored"})
    assert out == {"run_id": run_id, "node": "collect", "status": "pending", "attempt": 3, "runner_id": "daily_resume-x"}
    assert order == ["check", "ready", "lock", ("submit", "daily_resume")]
    monkeypatch.setattr(locks, "is_running", lambda: True)
    with pytest.raises(locks.DailyBusy):
        jobs.resume_daily({"run_id": run_id, "node": "collect"})
    assert order.count(("submit", "daily_resume")) == 1                  # a busy lock submits nothing


def test_resume_daily_without_migration_029_is_503_and_submits_nothing(monkeypatch):
    """Task-6 review M1: the 029 gate of the resume runs in the synchronous pre-check, so the endpoint answers 503
    ``migration_029_missing`` instead of a 202 whose background run fails unseen."""
    from investment_assistant.tasks import daily
    from investment_assistant.core import locks
    import investment_assistant.core.tx as service
    import investment_assistant.workflow.graph as workflow

    def not_ready():
        raise service.Unavailable("migration_029_missing")

    monkeypatch.setattr(workflow, "resume_check", lambda rid, node, *, now: {"attempt": 1})
    monkeypatch.setattr(daily, "ensure_ready", not_ready)
    monkeypatch.setattr(locks, "is_running", lambda: False)
    monkeypatch.setattr(jobs.runner, "submit", lambda *a, **k: pytest.fail("must not submit"))
    with pytest.raises(service.Unavailable, match="migration_029_missing"):
        jobs.resume_daily({"run_id": "daily-20261030033000-1a2b3c4d", "node": "collect"})


@pytest.mark.parametrize("body", [None, {}, {"run_id": "daily-20261030033000-1a2b3c4d"}, {"node": "collect"},
                                  {"run_id": "x", "node": "collect"}, {"run_id": 1, "node": "collect"}])
def test_resume_daily_refuses_a_bad_body_before_any_check(monkeypatch, body):
    import investment_assistant.workflow.graph as workflow

    monkeypatch.setattr(workflow, "resume_check", lambda *a, **k: pytest.fail("the body is checked first"))
    with pytest.raises(ValueError):
        jobs.resume_daily(body)
