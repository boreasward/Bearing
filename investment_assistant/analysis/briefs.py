"""Brief rows for decision units, built from agent runs (P4 design §7.1).

The model never writes a brief directly: for_run derives the row from a
propose or interpret run, and rule_summary keeps the rule-only fallback.
The Codex/DeepSeek callers were retired with P4."""
from __future__ import annotations

from typing import Any

from investment_assistant.analysis.agent import REASON_TEXT


def rule_summary(unit, candidates) -> dict[str, Any]:
    """The rule-only summary, wording unchanged from the old fallback."""
    rules = '、'.join(f"{c['rule_id']}({c['action']})" for c in candidates)
    labels = f"；标签：{'、'.join(unit['labels'])}" if unit.get('labels') else ''
    return {'summary': f"规则候选：{rules}；建议动作 {unit['proposed_action']}{labels}。LLM 未参与。",
            'support': [], 'contra': [], 'conflicts': []}


def for_run(run: dict[str, Any], calls: list[dict[str, Any]], *, unit, candidates) -> dict[str, Any]:
    """A signal_briefs row from one agent run (§7.1): propose ok/pass carries
    thesis and counter risks, interpret ok carries its own checked output, a
    rejected interpret hides content, everything else falls back to rules."""
    brief = {'ticker': unit['ticker'], 'signal_date': unit['signal_date'], 'model': run['model'],
             'prompt_version': run['agent_version'], 'cutoff_at': run['cutoff_at'],
             'input_refs': sorted((f"call:{c['seq']}" for c in calls),
                                  key=lambda ref: int(ref.rsplit(':', 1)[1])),
             'agent_run_id': run['id']}
    mode, status, reason = run['mode'], run['status'], run.get('reason')
    if mode == 'propose' and status in ('ok', 'pass'):
        output = run['output'] or {}
        counter = run.get('counter') or {}
        risks = counter.get('risks') if counter.get('status') == 'ok' else []
        return {**brief, 'output': {'summary': output['summary'],
                                    'support': output.get('thesis') or [],
                                    'contra': risks or [],
                                    'conflicts': []},
                'status': 'ok', 'error': None}
    if mode == 'interpret' and status == 'ok':
        return {**brief, 'output': dict(run['output']), 'status': 'ok', 'error': None}
    if mode == 'interpret' and status == 'rejected':
        return {**brief, 'output': {}, 'status': 'error', 'error': REASON_TEXT[reason]}
    return {**brief, 'output': rule_summary(unit, candidates), 'status': 'fallback',
            'error': REASON_TEXT[reason]}
