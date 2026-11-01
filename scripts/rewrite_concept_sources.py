"""One-time Task1 source-path migration; emit a patch, preserving card versions.

Run from the repository root: python scripts/rewrite_concept_sources.py
Apply the emitted patch, then use --check to verify that no old sources remain.
Split files resolve #function anchors against their explicit destination list;
unanchored sources use the primary destination in MODULES. Already migrated
sources are left byte-for-byte intact. Only sources: lines are rewritten.
"""
from __future__ import annotations

import argparse
import ast
import difflib
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREFIX = 'investment_assistant/'
MODULES = {
    'workbench/account': 'execution/account_records',
    'workbench/account_views': 'views/account',
    'workbench/accounts': 'execution/accounts',
    'workbench/agent/__init__': 'analysis/agent/__init__',
    'workbench/agent/__main__': 'analysis/agent/__main__',
    'workbench/agent/chatgpt': 'core/llm/chatgpt',
    'workbench/agent/citations': 'execution/citations',
    'workbench/agent/conditions': 'execution/conditions',
    'workbench/agent/discuss_tools': 'review/discuss_tools',
    'workbench/agent/graph': 'analysis/agent/graph',
    'workbench/agent/growth_check': 'review/agent_runs',
    'workbench/agent/model': 'core/llm/model',
    'workbench/agent/plan_check': 'execution/plan_check',
    'workbench/agent/portfolio_check': 'execution/portfolio_check',
    'workbench/agent/tools': 'analysis/agent/tools',
    'workbench/agent_connection': 'core/llm/connection',
    'workbench/backcheck': 'optimize/backcheck',
    'workbench/bars': 'data/adapters/bars',
    'workbench/briefs': 'analysis/briefs',
    'workbench/chart': 'views/chart',
    'workbench/collect': 'data/collect',
    'workbench/data_view': 'views/data',
    'workbench/derivation': 'derivation/chain',
    'workbench/derivation_view': 'views/derivation',
    'workbench/digest': 'review/push_digest',
    'workbench/discipline': 'review/discipline',
    'workbench/discussion': 'review/discussion',
    'workbench/earnings': 'data/adapters/earnings',
    'workbench/entry_rules': 'execution/entry_rules',
    'workbench/execution': 'execution/adoption',
    'workbench/exits': 'execution/exits',
    'workbench/families': 'data/families',
    'workbench/instrument_view': 'views/instrument',
    'workbench/knowledge': 'core/knowledge',
    'workbench/knowledge_items': 'review/knowledge/items',
    'workbench/knowledge_perf': 'review/knowledge/perf',
    'workbench/labels': 'core/texts',
    'workbench/ledger': 'execution/ledger',
    'workbench/macro': 'data/adapters/fred',
    'workbench/monthly': 'review/monthly',
    'workbench/my_knowledge': 'review/knowledge/my_knowledge',
    'workbench/news': 'data/adapters/news',
    'workbench/paper_accounts': 'execution/paper/accounts',
    'workbench/paper_fill': 'execution/paper/fill',
    'workbench/paper_strategies': 'execution/paper/strategies',
    'workbench/plan_review': 'workflow/nodes/review',
    'workbench/plan_shadow': 'review/shadow',
    'workbench/plan_table': 'views/plan_table',
    'workbench/plan_views': 'views/plan_table',
    'workbench/plans': 'execution/plan_levels',
    'workbench/pool': 'analysis/pool',
    'workbench/positions': 'execution/positions',
    'workbench/reasoning': 'views/reasoning',
    'workbench/regime': 'analysis/regime',
    'workbench/repository': 'data/repo',
    'workbench/retro': 'review/retro',
    'workbench/retro_cards': 'review/retro_cards',
    'workbench/review': 'review/outcomes',
    'workbench/review_digest': 'review/period_digest',
    'workbench/rules': 'analysis/rules',
    'workbench/run_detail': 'views/runs/detail',
    'workbench/run_overview': 'views/runs/overview',
    'workbench/screen': 'analysis/screen_rules',
    'workbench/screening': 'analysis/screening',
    'workbench/service': 'workflow/batch',
    'workbench/stages': 'core/stages',
    'workbench/synthesis': 'analysis/synthesis',
    'workbench/timeline': 'execution/timeline',
    'workbench/tracks': 'review/tracks',
    'workbench/universe': 'data/adapters/universe',
    'workbench/user_inputs': 'execution/user_inputs',
    'workbench/workflow': 'workflow/graph',
    'filings/analysis': 'data/sec/extraction',
    'filings/monitor': 'data/sec/monitor',
    'filings/monitor_source': 'data/sources/edgar',
    'filings/report': 'data/sec/report',
    'filings/sec_downloader': 'data/adapters/sec_text',
    'filings/service': 'data/sec/storage',
    'us_research/domain': 'data/fundamentals',
    'us_research/guidance': 'analysis/fundamentals',
    'us_research/metrics': 'derivation/indicators/fiscal',
    'us_research/repository': 'data/repo',
    'us_research/service': 'data/acquisition',
    'us_research/sources': 'data/sources/fundamentals',
    'us_research/templates': 'analysis/fundamental_templates',
    'tasks/runner': 'core/runner',
}

# Explicit split destinations, in ownership order (core helpers precede consumers).
# AST is used only to read symbol names, never to rewrite production code.
SPLITS = {
    'workbench/plans': 'derivation/indicators/price execution/plan_levels',
    'workbench/timeline': 'analysis/agent/evidence execution/timeline',
    'workbench/agent/graph': 'review/agent_runs analysis/agent/graph',
    'workbench/agent/tools': 'derivation/indicators/price analysis/agent/tools',
    'workbench/agent/growth_check': 'core/knowledge review/agent_runs',
    'workbench/chart': 'derivation/indicators/price views/chart',
    'workbench/derivation': 'workflow/check derivation/chain',
    'workbench/families': 'core/redact workflow/targets data/families',
    'workbench/knowledge_items': 'core/knowledge review/knowledge/items',
    'workbench/paper_accounts': 'workflow/nodes/accounts analysis/agent/runner execution/paper/accounts',
    'workbench/plan_review': 'execution/chains analysis/agent/runner workflow/nodes/review',
    'workbench/plan_shadow': 'execution/simulation review/shadow',
    'workbench/regime': 'derivation/indicators/breadth analysis/regime',
    'workbench/screen': 'derivation/indicators/screen_metrics analysis/screen_rules',
    'workbench/screening': 'workflow/screen_collection analysis/screening',
    'workbench/stages': 'core/stages workflow/recorder',
    'workbench/repository': 'core/dbutil core/schema data/repo analysis/repo execution/repo '
                            'execution/repo_legacy review/repo workflow/repo optimize/repo',
    'workbench/service': 'core/tx core/constants core/llm/model execution/plan_context execution/chains '
                        'execution/trades analysis/units analysis/agent/runner review/shadow review/weekly '
                        'optimize/backcheck workflow/state workflow/batch workflow/nodes/collect '
                        'workflow/nodes/screen workflow/nodes/signals workflow/nodes/plan workflow/nodes/push workflow/nodes/retro '
                        'workflow/screen_collection views/today views/derivation views/account '
                        'views/agent_runs views/screen views/instrument',
    'us_research/service': 'analysis/fundamentals data/acquisition',
    'filings/monitor': 'tasks/filings data/sec/monitor',
}

# Renamed functions and ambiguous entrypoints retain a valid destination anchor.
RENAMED = {
    ('workbench/screen', 'main'): ('analysis/screening', 'main'),
    ('workbench/service', '_run_agent_unit'): ('analysis/agent/runner', 'run_agent_unit'),
    ('workbench/service', '_run_interpret_unit'): ('analysis/agent/runner', 'run_interpret_unit'),
    ('workbench/service', '_agent_stage'): ('analysis/agent/runner', 'agent_stage'),
    ('workbench/paper_accounts', 'decide_agent'): ('analysis/agent/runner', 'run_portfolio_decision'),
    ('workbench/paper_accounts', 'run_stage'): ('workflow/nodes/accounts', 'run_stage'),
    ('workbench/paper_accounts', 'main'): ('views/account', 'main'),
    ('workbench/plan_review', '_review_chain'): ('analysis/agent/runner', 'run_chain_review'),
    ('workbench/derivation', 'main'): ('workflow/check', 'main'),
    ('workbench/repository', 'lock'): ('core/dbutil', 'lock'),
    ('workbench/repository', 'knowledge_rows'): ('data/repo', 'knowledge_rows'),
    ('workbench/repository', 'insert_knowledge_citations'): ('analysis/repo', 'insert_knowledge_citations'),
}
for _stage in ('collect', 'screen', 'regime', 'agent', 'review', 'accounts', 'push', 'retro'):
    RENAMED['workbench/service', f'stage_{_stage}'] = (f'workflow/nodes/{_stage}', 'run')


@lru_cache(maxsize=None)
def symbols(module: str) -> set[str]:
    tree = ast.parse((ROOT / PREFIX / f'{module}.py').read_text(encoding='utf-8'))
    return {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def rewrite_source(source: str) -> str:
    path, sep, anchor = source.partition('#')
    if not path.startswith(PREFIX) or not path.endswith('.py'):
        return source
    old = path[len(PREFIX):-3]
    if old not in MODULES:
        return source
    module = MODULES[old]
    if anchor:
        if (old, anchor) in RENAMED:
            module, anchor = RENAMED[old, anchor]
        else:
            module = next((m for m in SPLITS.get(old, '').split() if anchor in symbols(m)), module)
    return f'{PREFIX}{module}.py{sep}{anchor}'


def rewrite(text: str) -> str:
    return re.sub(r'^sources:.*$', lambda line: re.sub(
        r'investment_assistant/[\w/]+\.py(?:#[\w.]+)?',
        lambda source: rewrite_source(source.group()), line.group()), text, flags=re.MULTILINE)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='fail if any source still needs migration')
    args = parser.parse_args()
    changed = []
    for path in sorted((ROOT / 'docs/knowledge/concepts').glob('*.md')):
        before = path.read_text(encoding='utf-8')
        after = rewrite(before)
        if before != after:
            changed.append((path.relative_to(ROOT), before, after))
    if args.check:
        for path, _, _ in changed:
            print(path)
        return bool(changed)
    print('*** Begin Patch')
    for path, before, after in changed:
        print(f'*** Update File: {path}')
        diff = list(difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm=''))
        for line in diff[2:]:
            print('@@' if line.startswith('@@') else line)
    print('*** End Patch')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
