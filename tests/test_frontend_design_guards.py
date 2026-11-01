"""Static design guards over `web/src` (v2.10 spec §6, plan Task 4).

Offline: reads source files and backend constants, no database, no network, no build.

Eight blocking guards plus self-checks. Every guard is fully blocking: there is no baseline and no per-file allowance
(the v2.10 migration shards were retired in Task 8; `test_baseline_retired` keeps the directory from coming back)."""
import re
from pathlib import Path

import pytest

from investment_assistant.tasks import daily
from investment_assistant.analysis import agent
from investment_assistant.review import retro
from investment_assistant.execution import timeline
from investment_assistant.execution import adoption as execution

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'web' / 'src'
BASELINE_DIR = Path(__file__).resolve().parent / 'frontend_design_baseline'  # retired: must not exist
MIGRATIONS = ROOT / 'migrations'
STATUS_TS = SRC / 'lib' / 'ui' / 'domain' / 'status.ts'

# --------------------------------------------------------------------------------------------------------------- patterns

_PALETTE = ('red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|'
            'slate|gray|zinc|neutral|stone')
_UTILITY = ('text|bg|border(?:-[trblxyse])?|ring(?:-offset)?|fill|stroke|divide|outline|decoration|from|via|to|'
            'shadow|accent|caret|placeholder')
RAW_PALETTE_RE = re.compile(rf'(?<![\w-])(?:[\w-]+:)*(?:{_UTILITY})-(?:{_PALETTE})-\d{{2,3}}')
# `white` / `black` are raw colours too (the tokens are background / foreground)
BLACK_WHITE_RE = re.compile(rf'(?<![\w-])(?:{_UTILITY})-(?:white|black)\b')
# `#abc` style literals; the lookbehind skips `&#123;` entities and `foo#bar`, the lookahead skips hash routes such as
# `#plan/…` and `#a-b`.
HEX_RE = re.compile(r'(?<![&\w])#[0-9a-fA-F]{3,8}\b(?![\w/-])')
COLOR_FN_RE = re.compile(r'\b(?:rgba?|hsla?|oklch)\(')
EMOJI_RE = re.compile('[\U0001F000-\U0001FAFF☀-➿⌀-⏿⬀-⯿️]')
STYLE_RE = re.compile(r'<style\b')
BUTTON_RE = re.compile(r'<button(?![\w-])')
DIALOG_RES = (
    re.compile(r'\b(?:window|globalThis)\??\.(?:confirm|alert|prompt)\b'),
    re.compile(r'(?<![\w.$])(?:alert|prompt)\('),
    # a bare `confirm('…')` is the native dialog; a local `function confirm()` (FetchDateModal) or `x.confirm(` is not
    re.compile(r'(?<![\w.$])(?<!function )confirm\(\s*[\'"`]'),
)
# a real tag is `{@html expr}`; prose that mentions the bare `{@html}` is not one
HTML_RE = re.compile(r'\{@html\s+\S')

# Permanent exemptions (relative to web/src, posix).
STYLE_EXEMPT = ('lib/charts/', 'routes/UiGallery.svelte')          # prefix / exact
DIALOG_EXEMPT = ('lib/navigation-guard.ts',)                       # Q1: synchronous unsaved-edit confirm
BUTTON_SCOPES = ('routes/', 'lib/components/', 'lib/charts/')      # `lib/ui/**` is where the real <button> lives
HTML_ALLOWED = {'routes/Knowledge.svelte': 2}                      # Q2: concept card body + principle section body

# ---------------------------------------------------------------------------------------------------------------- counting

def count_raw_palette(text: str) -> int:
    return len(RAW_PALETTE_RE.findall(text)) + len(BLACK_WHITE_RE.findall(text))


def count_hex_rgb(text: str) -> int:
    return len(HEX_RE.findall(text)) + len(COLOR_FN_RE.findall(text))


def count_emoji(text: str) -> int:
    return len(EMOJI_RE.findall(text))


def count_style_block(text: str) -> int:
    return len(STYLE_RE.findall(text))


def count_native_button(text: str) -> int:
    return len(BUTTON_RE.findall(text))


def count_native_dialog(text: str) -> int:
    return sum(len(rx.findall(text)) for rx in DIALOG_RES)


def count_html(text: str) -> int:
    return len(HTML_RE.findall(text))


def scanned_files() -> list[Path]:
    """Every `.svelte` / `.ts` under web/src except tests, fixtures and the vitest setup."""
    found = []
    for path in sorted(SRC.rglob('*')):
        if path.suffix not in ('.svelte', '.ts') or not path.is_file():
            continue
        name = path.name
        if name.endswith('.test.ts') or name.endswith('-fixtures.ts') or name == 'vitest-setup.ts':
            continue
        found.append(path)
    return found


def rel(path: Path) -> str:
    return path.relative_to(SRC).as_posix()


def _read(path: Path) -> str:
    return path.read_text(encoding='utf-8')


def count_check(check: str, path: Path) -> int:
    """Violations of one counted check in one file, with the scope rules of that check applied."""
    r = rel(path)
    is_svelte = path.suffix == '.svelte'
    text = _read(path)
    if check == 'raw_palette':
        return count_raw_palette(text)
    if check == 'hex_rgb':
        return count_hex_rgb(text)
    if check == 'emoji':
        return count_emoji(text)
    if check == 'style_block':
        if not is_svelte or r.startswith(STYLE_EXEMPT[0]) or r == STYLE_EXEMPT[1]:
            return 0
        return count_style_block(text)
    if check == 'native_button':
        if not is_svelte or not r.startswith(BUTTON_SCOPES):
            return 0
        return count_native_button(text)
    raise KeyError(check)


def counts_by_file(check: str) -> dict[str, int]:
    """Relative path -> violation count, only files with at least one violation."""
    result = {}
    for path in scanned_files():
        n = count_check(check, path)
        if n:
            result[rel(path)] = n
    return result


# ---------------------------------------------------------------------------------------------------------------- helpers

def _hits(violations: list[str], title: str, hint: str = ''):
    assert not violations, f'{title}{hint}:\n  ' + '\n  '.join(violations)


def assert_clean(check: str, hint: str):
    found = [f'{file}: {n}' for file, n in sorted(counts_by_file(check).items())]
    _hits(found, f'{check}: violations found', hint)


# ------------------------------------------------------------------------------------------------------------------ tests

def test_no_raw_palette_classes():
    """Tailwind palette colours (`text-red-600`) and `text/bg/border-white|black` are banned; use semantic tokens."""
    assert_clean('raw_palette', ' — use semantic token classes (text-up / text-down / bg-muted …)')


def test_no_hex_or_rgb_literals():
    """Colours come from `tokens.css` (and the chart theme reading it), never inline literals."""
    assert_clean('hex_rgb', ' — use token classes or `var(--token)` (charts: lib/charts/theme.ts)')


def test_no_emoji():
    """Emoji and pictographs are banned; icons come from `Icon` (lucide). Arrows, ① and ▲ ● are typography."""
    assert_clean('emoji', ' — use <Icon> or plain text')


def test_no_style_blocks():
    """No component `<style>` blocks (charts and the gallery excepted)."""
    assert_clean('style_block', ' — use Tailwind classes or styles/*.css')


def test_no_native_buttons():
    """Pages and components use `Button`; only `lib/ui/**` writes `<button>`."""
    assert_clean('native_button', ' — use <Button> from $lib/ui')


def test_no_native_dialogs():
    """No `window.confirm/alert/prompt` or bare `alert(` / `prompt(`; `navigation-guard.ts` is the one exemption (Q1)."""
    bad = []
    for path in scanned_files():
        if rel(path) in DIALOG_EXEMPT:
            continue
        n = count_native_dialog(_read(path))
        if n:
            bad.append(f'{rel(path)}: {n}')
    _hits(bad, 'native dialogs found', ' — use ConfirmDialog / notify')


def test_html_only_in_knowledge():
    """`{@html` appears exactly twice in routes/Knowledge.svelte (Q2) and nowhere else."""
    found = {}
    for path in scanned_files():
        n = count_html(_read(path))
        if n:
            found[rel(path)] = n
    assert found == HTML_ALLOWED, f'{{@html}} locations changed: {found}, expected {HTML_ALLOWED}'


def test_baseline_retired():
    """The v2.10 baseline shards are gone for good: every guard is fully blocking."""
    assert not BASELINE_DIR.exists(), f'{BASELINE_DIR} must not exist (guards have no allowance)'


# Backend vocabulary: the status.ts TONES table must know every status the back end can emit.

def last_check_values(sql_files: list[str], table: str, column: str) -> set[str]:
    """Values of the `{column} IN (…)` CHECK of `table` as the migrations leave it: scans the files in order and
    keeps the last definition (the CREATE TABLE column check, or a later `ALTER TABLE … ADD CONSTRAINT … CHECK`)."""
    create = re.compile(rf'CREATE TABLE (?:IF NOT EXISTS )?{table}\s*\((.*?)\n\);', re.S)
    inline = re.compile(rf'\b{column}\s+TEXT[^,]*?CHECK\s*\(\s*{column}\s+IN\s*\(([^)]*)\)\)', re.S)
    alter = re.compile(rf'ALTER TABLE\s+(?:ONLY\s+)?{table}\s+ADD CONSTRAINT\s+\w+\s+'
                       rf'CHECK\s*\(\s*{column}\s+IN\s*\(([^)]*)\)\s*\)', re.S)
    last = None
    for sql in sql_files:
        found = []                                               # (position in file, quoted list)
        for block in create.finditer(sql):
            check = inline.search(block.group(1))
            if check:
                found.append((block.start(), check.group(1)))
        found.extend((m.start(), m.group(1)) for m in alter.finditer(sql))
        if found:
            last = max(found)[1]
    assert last is not None, f'no `{column} IN (…)` CHECK found for table {table}'
    return set(re.findall(r"'([^']+)'", last))


def _sql_check_values(table: str, column: str) -> set[str]:
    return last_check_values([p.read_text(encoding='utf-8') for p in sorted(MIGRATIONS.glob('*.sql'))], table, column)


def _tones_by_domain() -> dict[str, set[str]]:
    text = _read(STATUS_TS)
    body = re.search(r'export const TONES\s*=\s*\{\n(.*?)\n\}\s*as const satisfies', text, re.S)
    assert body, 'status.ts: `export const TONES = { … } as const satisfies …` not found'
    domains = {}
    for line in body.group(1).splitlines():
        if not line.strip() or line.strip().startswith('//'):
            continue
        match = re.match(r'^\s*(\w+):\s*\{(.*)\},?\s*(?://.*)?$', line)
        assert match, f'status.ts: each domain must be on one line `domain: {{ key: \'tone\', … }},` — got {line!r}'
        keys = re.findall(r"[{,]\s*(?:(\w+)|'([^']+)')\s*:\s*'\w+'", '{' + match.group(2) + '}')
        domains[match.group(1)] = {bare or quoted for bare, quoted in keys}
    return domains


def test_status_vocabulary_complete():
    """Each StatusPill domain knows every value its backend source can produce (spec §4.4, D16)."""
    node = _sql_check_values('stage_runs', 'status') | {'pending', 'running'}
    expected = {
        'node': node,
        'run': node | set(daily.JOB_STATUS.values()),
        'chain': set(timeline.CHAIN_STATES),
        'fetch': _sql_check_values('data_fetch_runs', 'status'),
        'agent': set(agent.STATUSES),
        'connection': _sql_check_values('agent_connections', 'status'),
        'card': set(retro.CARD_STATUSES),
        'execution': set(execution.STATUSES) | set(execution.CHAIN_STATUSES) | set(retro.EXECUTION_STATUSES),
        'knowledge': _sql_check_values('knowledge_status', 'status'),
        'instruction': set(timeline.INSTRUCTION_TEXT),
    }
    tones = _tones_by_domain()
    assert set(tones) >= set(expected), f'missing domains: {sorted(set(expected) - set(tones))}'
    missing = {domain: sorted(values - tones[domain]) for domain, values in expected.items()
               if values - tones[domain]}
    assert not missing, f'status.ts TONES lacks backend values: {missing}'


# ---------------------------------------------------------------------------------------------------- guard self-checks

def test_guard_patterns_bite_and_ignore_false_positives():
    """Each regex catches a real violation and stays quiet on look-alikes (hash routes, entities, CSS vars, typography)."""
    # raw palette
    assert count_raw_palette('class="text-red-600 hover:bg-amber-700/50 dark:border-slate-200"') == 3
    assert count_raw_palette('class="md:from-blue-500 ring-offset-white text-black bg-white"') == 4
    # side / offset / shadow / accent / caret / placeholder / fill / stroke utilities
    assert count_raw_palette('border-t-red-500 border-x-gray-200 border-e-sky-400 border-y-white border-l-black') == 5
    assert count_raw_palette('ring-offset-slate-100 shadow-blue-500 accent-pink-500 caret-lime-400 placeholder-gray-400') == 5
    assert count_raw_palette('fill-white stroke-black ring-white outline-black divide-white decoration-black via-white to-black') == 8
    assert count_raw_palette('border border-t border-x ring ring-2 shadow-md shadow-sm outline-none divide-y accent-auto') == 0
    assert count_raw_palette('border-border ring-ring placeholder-muted-foreground photo-white auto-black') == 0
    assert count_raw_palette('class="text-up bg-muted border-border text-foreground bg-card text-whitespace"') == 0
    # hex / rgb
    assert count_hex_rgb("color: '#26a69a'; fill: #fff; x: #ff000080") == 3
    assert count_hex_rgb('rgba(0,0,0,.5) hsl(10 20% 30%) oklch(0.5 0.1 20) rgb(1,2,3)') == 4
    assert count_hex_rgb('href="#plan/12" href="#data" href="#discuss/ab" &#128512; &#x27; #_ui var(--up) color-mix(in oklab, red 20%, blue)') == 0
    assert count_hex_rgb('href="#review/abc" href="#a-b" const rgbaLike = 1; frgb(1)') == 0
    # emoji
    assert count_emoji('✅ 成功 \U0001F680 ⚠️ ⏰ ⭐') == 6
    assert count_emoji('① → ↗ ▲ ● ▼ — · … ×') == 0
    # style / button / html / dialogs
    assert count_style_block('<script></script>\n<style>\n.a{}\n</style>') == 1
    assert count_style_block('<styled-thing> style="x" <svelte:head>') == 0
    assert count_native_button('<button type="button">x</button> <button\n class="a">') == 2
    assert count_native_button('<Button>x</Button> <buttons> <button-group>') == 0
    assert count_html('{@html body}\n{@html other}') == 2
    assert count_html('{html} @html mention of {@html} in a comment') == 0
    assert count_native_dialog('if (window.confirm("x")) {} window.alert(1); prompt("y"); alert(2)') == 4
    assert count_native_dialog('window?.confirm("x"); globalThis.alert(1); globalThis.prompt("y"); globalThis.confirm(1)') == 4
    assert count_native_dialog('if (confirm("sure?")) {} confirm(\'x\'); confirm(`y`); confirm( "z")') == 4
    assert count_native_dialog('notify.success("x"); confirmDialog(); this.alert(1); toast.alert(2); prompts(1); myprompt(1)') == 0
    assert count_native_dialog('function confirm() { go() } onclick={confirm} dialog.confirm("x"); x.confirm(1); confirm()') == 0


def test_sql_check_extraction_follows_later_alter_constraint():
    """The vocabulary comes from the LAST definition of a CHECK, including `ALTER TABLE … ADD CONSTRAINT` in later migrations."""
    create = """CREATE TABLE IF NOT EXISTS runs (
  id   INT,
  status TEXT NOT NULL CHECK (status IN ('a', 'b')),
  note TEXT
);
"""
    alter = """ALTER TABLE runs DROP CONSTRAINT IF EXISTS runs_status_check;
ALTER TABLE runs ADD CONSTRAINT runs_status_check
  CHECK (status IN ('a', 'b', 'c'));
"""
    later = "ALTER TABLE runs ADD CONSTRAINT runs_status_check2 CHECK (status IN ('a', 'd'));\n"
    other = "ALTER TABLE other ADD CONSTRAINT x CHECK (status IN ('zzz'));\n"
    assert last_check_values([create], 'runs', 'status') == {'a', 'b'}
    assert last_check_values([create, alter, other], 'runs', 'status') == {'a', 'b', 'c'}
    assert last_check_values([create, alter, later], 'runs', 'status') == {'a', 'd'}
    assert last_check_values([alter + later + create.replace("'b')", "'b', 'e')")], 'runs', 'status') == {'a', 'b', 'e'}
    with pytest.raises(AssertionError):
        last_check_values([create], 'runs', 'stage')
    # real migrations: stage_runs.stage was widened by 026, 027 and 028; the last one wins
    stages = _sql_check_values('stage_runs', 'stage')
    assert {'sec_monitor', 'retro', 'collect', 'push'} <= stages


def test_scan_is_not_vacuous():
    scanned = {rel(p) for p in scanned_files()}
    assert len(scanned) > 50, f'only {len(scanned)} files scanned'
    for known in ('routes/Today.svelte', 'routes/Knowledge.svelte', 'lib/components/PlanTable.svelte',
                  'lib/navigation-guard.ts', 'lib/api.ts', 'lib/charts/CandleChart.svelte', 'app.svelte'):
        assert known in scanned, f'{known} is not scanned'
    assert not any(name.endswith('.test.ts') or name.endswith('-fixtures.ts') for name in scanned)
