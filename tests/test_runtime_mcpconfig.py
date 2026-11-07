"""The per-run .pi/mcp.json: the tool surface must find the database even though Pi starts
it with an allow-listed environment (pi-unattended.md: HOME, PATH, proxies, PI_* only)."""

import json
from datetime import date

from investment_assistant.runtime import mcpconfig


def _args(path):
    return json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["ia"]["args"]


def test_defaults_to_the_repo_env_file_so_the_tool_surface_reaches_the_database(tmp_path):
    """2026-11-07：页面对话的工具全部返回 unavailable——Pi 的环境白名单滤掉了连接串，对话路径又没传
    --env-file。没指定时用仓库根目录的 .env（存在才加）。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".env").write_text("INVESTMENT_ASSISTANT_DATABASE_URL=postgresql://x\n", encoding="utf-8")
    args = _args(mcpconfig.write(tmp_path / "w1", repo_root=repo, session=date(2026, 11, 6), mode="chat", agent_session="c-1"))
    assert args[args.index("--env-file") + 1] == str(repo / ".env")


def test_no_env_file_argument_when_the_repo_has_none(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    args = _args(mcpconfig.write(tmp_path / "w2", repo_root=repo, session=date(2026, 11, 6), mode="chat", agent_session="c-2"))
    assert "--env-file" not in args


def test_explicit_env_file_wins(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".env").write_text("", encoding="utf-8")
    pinned = tmp_path / "pinned.env"
    args = _args(
        mcpconfig.write(
            tmp_path / "w3", repo_root=repo, session=date(2026, 11, 6), mode="brief", agent_session="b-1", env_file=pinned
        )
    )
    assert args[args.index("--env-file") + 1] == str(pinned)
