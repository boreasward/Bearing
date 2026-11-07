"""agent 工作区（spec §9.5）：两个说明文件存在、非空、不含「降级」，说明文件含止损纪律增补。离线。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTRUCTIONS = ROOT / "agent" / "instructions.md"
PLAYBOOK = ROOT / "agent" / "playbook.md"


def test_workspace_files_exist_and_are_non_empty():
    for path in (INSTRUCTIONS, PLAYBOOK):
        assert path.is_file(), path
        assert path.read_text(encoding="utf-8").strip()


def test_instructions_carry_the_discipline_addendum():
    text = INSTRUCTIONS.read_text(encoding="utf-8")
    assert "不给出也不修改止损价、卖出价和股数" in text
    assert "只引用工具返回" in text


def test_workspace_never_says_degradation():
    for path in (INSTRUCTIONS, PLAYBOOK):
        assert "降级" not in path.read_text(encoding="utf-8")
