/** 统一展示到秒的北京时间，末尾带「北京」；非法/空值兜底，不跟浏览器时区走 */
export function fmtBeijing(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  const d = new Date(String(value))
  if (Number.isNaN(d.getTime())) return String(value)
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
  }).formatToParts(d)
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? ''
  return `${get('year')}-${get('month')}-${get('day')} ${get('hour')}:${get('minute')}:${get('second')} 北京`
}
