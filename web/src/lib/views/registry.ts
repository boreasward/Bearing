import type { Component } from 'svelte'
import { TOOL_LABELS } from './labels'
import AccountCard from './cards/AccountCard.svelte'
import AgendaCard from './cards/AgendaCard.svelte'
import BarsTable from './cards/BarsTable.svelte'
import GenericCard from './cards/GenericCard.svelte'
import LevelsCard from './cards/LevelsCard.svelte'
import MarketCard from './cards/MarketCard.svelte'
import NoteCard from './cards/NoteCard.svelte'
import PlanSnapshotCard from './cards/PlanSnapshotCard.svelte'
import ResearchSnapshotCard from './cards/ResearchSnapshotCard.svelte'
import TradeCard from './cards/TradeCard.svelte'

export type CallMeta = { tool: string; ref: string | null; session: string | null; calledAt: string | null; write?: 'written' | 'preview' | 'replayed' }
export type ViewEntry = { component: Component<{ data: unknown; meta: CallMeta }>; title(data: unknown): string }
export const TOOL_NAMES = ["cancel_plan", "compute_plan_levels", "conclude_research", "confirm_stop_order", "continue_plan", "drop_research", "ensure_data", "get_account", "get_agenda", "get_candidates", "get_concept", "get_coverage", "get_earnings", "get_filings", "get_fundamentals", "get_indicators", "get_market", "get_news", "get_plans", "get_positions", "get_price_bars", "get_research", "get_rotation_table", "raise_stop", "record_cash_flow", "record_equity", "record_trade", "revise_plan", "save_holding_review", "save_plan", "save_rotation", "save_summary", "save_verdict", "start_research", "void_record", "watchlist_add", "watchlist_remove", "withdraw_rotation"] as const
export type ToolName = typeof TOOL_NAMES[number]

export const REGISTRY: Record<ToolName, ViewEntry> = {
  cancel_plan: { component: PlanSnapshotCard, title: () => TOOL_LABELS.cancel_plan },
  compute_plan_levels: { component: LevelsCard, title: () => TOOL_LABELS.compute_plan_levels },
  conclude_research: { component: ResearchSnapshotCard, title: () => TOOL_LABELS.conclude_research },
  confirm_stop_order: { component: PlanSnapshotCard, title: () => TOOL_LABELS.confirm_stop_order },
  continue_plan: { component: PlanSnapshotCard, title: () => TOOL_LABELS.continue_plan },
  drop_research: { component: ResearchSnapshotCard, title: () => TOOL_LABELS.drop_research },
  ensure_data: { component: GenericCard, title: () => TOOL_LABELS.ensure_data },
  get_account: { component: AccountCard, title: () => TOOL_LABELS.get_account },
  get_agenda: { component: AgendaCard, title: () => TOOL_LABELS.get_agenda },
  get_candidates: { component: GenericCard, title: () => TOOL_LABELS.get_candidates },
  get_concept: { component: GenericCard, title: () => TOOL_LABELS.get_concept },
  get_coverage: { component: GenericCard, title: () => TOOL_LABELS.get_coverage },
  get_earnings: { component: GenericCard, title: () => TOOL_LABELS.get_earnings },
  get_filings: { component: GenericCard, title: () => TOOL_LABELS.get_filings },
  get_fundamentals: { component: GenericCard, title: () => TOOL_LABELS.get_fundamentals },
  get_indicators: { component: GenericCard, title: () => TOOL_LABELS.get_indicators },
  get_market: { component: MarketCard as ViewEntry['component'], title: () => TOOL_LABELS.get_market },
  get_news: { component: GenericCard, title: () => TOOL_LABELS.get_news },
  get_plans: { component: PlanSnapshotCard, title: () => TOOL_LABELS.get_plans },
  get_positions: { component: PlanSnapshotCard, title: () => TOOL_LABELS.get_positions },
  get_price_bars: { component: BarsTable, title: () => TOOL_LABELS.get_price_bars },
  get_research: { component: ResearchSnapshotCard, title: () => TOOL_LABELS.get_research },
  get_rotation_table: { component: GenericCard, title: () => TOOL_LABELS.get_rotation_table },
  raise_stop: { component: PlanSnapshotCard, title: () => TOOL_LABELS.raise_stop },
  record_cash_flow: { component: AccountCard, title: () => TOOL_LABELS.record_cash_flow },
  record_equity: { component: AccountCard, title: () => TOOL_LABELS.record_equity },
  record_trade: { component: TradeCard, title: () => TOOL_LABELS.record_trade },
  revise_plan: { component: PlanSnapshotCard, title: () => TOOL_LABELS.revise_plan },
  save_holding_review: { component: NoteCard, title: () => TOOL_LABELS.save_holding_review },
  save_plan: { component: PlanSnapshotCard, title: () => TOOL_LABELS.save_plan },
  save_rotation: { component: GenericCard, title: () => TOOL_LABELS.save_rotation },
  save_summary: { component: NoteCard, title: () => TOOL_LABELS.save_summary },
  save_verdict: { component: NoteCard, title: () => TOOL_LABELS.save_verdict },
  start_research: { component: ResearchSnapshotCard, title: () => TOOL_LABELS.start_research },
  void_record: { component: GenericCard, title: () => TOOL_LABELS.void_record },
  watchlist_add: { component: GenericCard, title: () => TOOL_LABELS.watchlist_add },
  watchlist_remove: { component: GenericCard, title: () => TOOL_LABELS.watchlist_remove },
  withdraw_rotation: { component: GenericCard, title: () => TOOL_LABELS.withdraw_rotation },
}
export function viewFor(tool: string): ViewEntry {
  return Object.hasOwn(REGISTRY, tool) ? REGISTRY[tool as ToolName] : { component: GenericCard, title: () => TOOL_LABELS[tool as ToolName] ?? tool }
}
