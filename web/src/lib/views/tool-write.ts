import { record, toolError } from './data'
import type { CallMeta } from './registry'

const MUTATING_TOOLS = new Set([
  'save_plan', 'save_verdict', 'save_holding_review', 'save_summary',
  'record_trade', 'record_cash_flow', 'record_equity',
  'start_research', 'conclude_research', 'drop_research',
  'continue_plan', 'revise_plan', 'cancel_plan', 'raise_stop', 'confirm_stop_order',
  'void_record', 'watchlist_add', 'watchlist_remove', 'ensure_data',
])

export function isMutatingTool(tool: string): boolean {
  return MUTATING_TOOLS.has(tool)
}

export function toolWriteStatus(tool: string, data: unknown, error?: string | null): CallMeta['write'] {
  if (!isMutatingTool(tool) || error || toolError(data)) return undefined
  const result = record(data)
  if (result.write === false) return 'preview'
  return result.replay === true ? 'replayed' : 'written'
}
