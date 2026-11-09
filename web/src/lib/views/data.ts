export type Row = Record<string, unknown>
export function record(value: unknown): Row {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Row : {}
}
export function records(value: unknown): Row[] {
  return Array.isArray(value) ? value.filter(v => v !== null && typeof v === 'object' && !Array.isArray(v)) as Row[] : []
}
export function num(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}
export function text(value: unknown): string {
  if (value === null || value === undefined || value === '') return '没算'
  if (typeof value === 'boolean') return value ? '是' : '否'
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}
export function toolError(value: unknown): string | null {
  const error = record(value).error
  if (!error) return null
  const errors: Record<string, string> = { no_run: '还没有运行记录', invalid_argument: '参数不正确', unknown_plan: '找不到这份计划', unknown_research: '找不到这份研究', unknown_call: '找不到这次调用记录', data_missing: '数据尚未准备好' }
  const raw = String(error)
  return errors[raw] ?? (/[\u3400-\u9fff]/.test(raw) ? raw.replace(/^[a-z_]+: /, '') : '工具调用失败，请稍后重试')
}
