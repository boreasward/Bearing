import re
from pathlib import Path

from investment_assistant.toolsurface.dispatch import tool_defs

ROW = re.compile(r"^\s*(\w+): \{ group: '(\w+)', write: '(none|direct|confirm)', summary: '", re.MULTILINE)
SOURCE = Path(__file__).parents[1] / "web/src/lib/views/tool-catalog.ts"


def _rows():
    return {name: write for name, _group, write in ROW.findall(SOURCE.read_text(encoding="utf-8"))}


def test_catalog_lists_exactly_the_chat_tools():
    assert set(_rows()) == {tool.name for tool in tool_defs("chat")}


def test_confirm_first_tools_are_the_ones_taking_confirmed():
    takes_confirmed = {t.name for t in tool_defs("chat") if "confirmed" in t.input_schema.get("properties", {})}
    assert {name for name, write in _rows().items() if write == "confirm"} == takes_confirmed
