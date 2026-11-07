"""The runtime settings (spec §9.2 / v3 设计 §15.4): runtime, provider and model live in the
database with no built-in defaults, are checked before saving and can be overridden per run.
Providers and models are runtime data read from the dedicated Pi directory — never hardcoded."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from investment_assistant.runtime.base import Runtime, RuntimeSpec
from investment_assistant.runtime.fake import FakeRuntime
from investment_assistant.runtime.pi import PiRuntime, pi_binary
from investment_assistant.runtime.pi import pi_dir as _default_pi_dir

RUNTIMES = ("pi", "fake")


def current(conn) -> RuntimeSpec | None:
    """The stored spec, only when all three keys are set."""
    from investment_assistant.facts import plan_repo

    row = plan_repo.settings(conn)
    spec = RuntimeSpec(runtime=row.get("runtime", ""), provider=row.get("provider", ""), model=row.get("model", ""))
    return spec if spec.runtime and spec.provider and spec.model else None


def options(pi_dir: Path) -> dict:
    """What a user can pick: the runtimes, the providers known to the Pi directory
    (models.json ∪ auth.json) and each provider's model ids from models.json."""
    providers: dict[str, list[str]] = {}
    auth_keys: set[str] = set()
    models_file = Path(pi_dir) / "models.json"
    if models_file.exists():
        try:
            data = json.loads(models_file.read_text(encoding="utf-8"))
            providers = {
                name: [str(model.get("id")) for model in (body or {}).get("models", []) if model.get("id")]
                for name, body in (data.get("providers") or {}).items()
            }
        except (ValueError, OSError, AttributeError):
            providers = {}
    auth_file = Path(pi_dir) / "auth.json"
    if auth_file.exists():
        try:
            auth = json.loads(auth_file.read_text(encoding="utf-8"))
            auth_keys = set(auth) if isinstance(auth, dict) else set()
        except (ValueError, OSError):
            auth_keys = set()
    return {
        "runtimes": list(RUNTIMES),
        "providers": sorted({*providers, *auth_keys}),
        "models": providers,
    }


def check(spec: RuntimeSpec, *, pi_dir: Path, run=subprocess.run) -> list[str]:
    """Why this spec cannot be saved; empty means it can. ``run`` is injectable for tests."""
    problems: list[str] = []
    if spec.runtime not in RUNTIMES:
        problems.append(f"未知运行时：{spec.runtime}（可选 {'、'.join(RUNTIMES)}）")
    if spec.runtime == "pi":
        opts = options(pi_dir)
        if spec.provider not in opts["providers"]:
            done = run(
                [str(pi_binary()), "auth", "check", "--provider", spec.provider, "--json"],
                capture_output=True,
                text=True,
            )
            if not _auth_ready(done):
                problems.append(f"提供方 {spec.provider} 未登录也未定义（pi auth check 不是 ready）")
    return problems


def _auth_ready(done) -> bool:
    try:
        return bool(json.loads(done.stdout or "").get("status") == "ready")
    except ValueError:
        return False


def save(conn, spec: RuntimeSpec, *, pi_dir: Path) -> None:
    """Check first, then persist the three keys (v3 设计 §15.4)."""
    problems = check(spec, pi_dir=pi_dir)
    if problems:
        raise ValueError("\n".join(problems))
    from investment_assistant.facts import plan_repo

    plan_repo.set_setting(conn, "runtime", spec.runtime)
    plan_repo.set_setting(conn, "provider", spec.provider)
    plan_repo.set_setting(conn, "model", spec.model)


def build(spec: RuntimeSpec) -> Runtime:
    """The implementation behind a runtime name."""
    if spec.runtime == "pi":
        return PiRuntime()
    if spec.runtime == "fake":
        return FakeRuntime([])
    raise ValueError(f"未知运行时：{spec.runtime}（可选 {'、'.join(RUNTIMES)}）")


def default_pi_dir() -> Path:
    """The dedicated Pi configuration directory (same override as PiRuntime)."""
    return _default_pi_dir()
