"""运行时设置：current 三项齐全才返回；options 读 pi 目录的 models.json/auth.json；check 给出
不可用原因；save 拒绝并保持库不动（v3 设计验收 12 的一半）。提供方与模型是运行期数据，不硬编码。"""

import json
from pathlib import Path

import pytest
from investment_assistant.runtime import settings
from investment_assistant.runtime.base import RuntimeSpec


def fake_run(out: dict, returncode: int = 0):
    """替身 pi auth check --provider X --json：stdout JSON + 退出码。"""

    class Done:
        def __init__(self):
            self.stdout = json.dumps(out)
            self.returncode = returncode

    return lambda *a, **k: Done()


def seed_pi_dir(tmp_path: Path) -> Path:
    pi_dir = tmp_path / "pi"
    pi_dir.mkdir()
    (pi_dir / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "prov-a": {"models": [{"id": "model-a1"}, {"id": "model-a2"}]},
                    "prov-b": {"models": []},
                }
            }
        ),
        encoding="utf-8",
    )
    (pi_dir / "auth.json").write_text(json.dumps({"prov-c": {"apiKey": "x"}}), encoding="utf-8")
    return pi_dir


def test_current_none_until_all_three(v3_tx):
    import investment_assistant.core.tx as core_tx
    from investment_assistant.facts import plan_repo

    with core_tx._tx() as conn:
        plan_repo.set_setting(conn, "runtime", "pi")
        plan_repo.set_setting(conn, "provider", "prov-a")
        assert settings.current(conn) is None  # 只有两项
        plan_repo.set_setting(conn, "model", "model-a1")
        assert settings.current(conn) == RuntimeSpec(runtime="pi", provider="prov-a", model="model-a1")


def test_options_reads_models_and_auth(tmp_path):
    opts = settings.options(seed_pi_dir(tmp_path))
    assert opts["runtimes"] == list(settings.RUNTIMES)
    assert set(opts["providers"]) == {"prov-a", "prov-b", "prov-c"}
    assert opts["models"] == {"prov-a": ["model-a1", "model-a2"], "prov-b": []}


def test_options_tolerates_missing_dir(tmp_path):
    opts = settings.options(tmp_path / "nope")
    assert opts == {"runtimes": list(settings.RUNTIMES), "providers": [], "models": {}}


def test_check_unknown_runtime():
    problems = settings.check(
        RuntimeSpec("nope", "prov-a", "model-a1"), pi_dir=Path("/nope"), run=fake_run({"status": "ready"})
    )
    assert len(problems) == 1 and "nope" in problems[0]


def test_check_pi_provider_unknown_and_not_ready(tmp_path):
    pi_dir = seed_pi_dir(tmp_path)
    spec = RuntimeSpec("pi", "prov-never", "model-x")
    ready = settings.check(spec, pi_dir=pi_dir, run=fake_run({"status": "ready"}))
    assert ready == []  # auth check 就绪，不必出现在 models.json 里
    not_ready = settings.check(spec, pi_dir=pi_dir, run=fake_run({"status": "not_ready"}, returncode=1))
    assert len(not_ready) == 1 and "prov-never" in not_ready[0]


def test_check_pi_known_provider_skips_auth(tmp_path):
    pi_dir = seed_pi_dir(tmp_path)

    def boom(*a, **k):
        raise AssertionError("已知提供方不应再跑 auth check")

    assert settings.check(RuntimeSpec("pi", "prov-a", "model-a1"), pi_dir=pi_dir, run=boom) == []


def test_check_fake_never_checks():
    assert settings.check(RuntimeSpec("fake", "whatever", "whatever"), pi_dir=Path("/nope"), run=None) == []


def test_save_rejects_and_keeps_store(v3_tx):
    import investment_assistant.core.tx as core_tx

    bad = RuntimeSpec("nope", "prov-a", "model-a1")
    with pytest.raises(ValueError) as exc, core_tx._tx() as conn:
        settings.save(conn, bad, pi_dir=Path("/nope"))
    assert "nope" in str(exc.value)
    with core_tx.read_only_tx() as conn:
        assert settings.current(conn) is None


def test_save_writes_all_three(v3_tx, tmp_path):
    import investment_assistant.core.tx as core_tx

    pi_dir = seed_pi_dir(tmp_path)
    spec = RuntimeSpec("pi", "prov-a", "model-a1")
    with core_tx._tx() as conn:
        settings.save(conn, spec, pi_dir=pi_dir)
    with core_tx.read_only_tx() as conn:
        assert settings.current(conn) == spec


def test_build_returns_the_implementation():
    assert settings.build(RuntimeSpec("fake", "p", "m")).name == "fake"
    assert settings.build(RuntimeSpec("pi", "p", "m")).name == "pi"
    with pytest.raises(ValueError):
        settings.build(RuntimeSpec("nope", "p", "m"))
