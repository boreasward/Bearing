"""The per-run project-level ``.pi/mcp.json`` (spec §9.5 / pi-unattended.md): one tool surface
process per run, pinned to one trading session, one mode and one agent-session id."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path


def write(
    workdir: Path,
    *,
    repo_root: Path,
    session: date,
    mode: str,
    agent_session: str,
    env_file: Path | None = None,
) -> Path:
    """Write ``<workdir>/.pi/mcp.json`` declaring the ``ia`` server (direct exposure, repo cwd);
    ``--env-file`` only when the caller pinned an env file."""
    args = [
        "-m",
        "investment_assistant.toolsurface",
        "--session",
        session.isoformat(),
        "--mode",
        mode,
        "--agent-session",
        agent_session,
    ]
    if env_file is not None:
        args += ["--env-file", str(env_file)]
    config = {
        "mcpServers": {
            "ia": {
                "command": str(repo_root / ".venv" / "bin" / "python"),
                "args": args,
                "cwd": str(repo_root),
                "exposure": "direct",
            }
        }
    }
    path = Path(workdir) / ".pi" / "mcp.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
