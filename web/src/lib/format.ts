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

/** A `YYYY-MM-DD` date as that day's local midnight with its UTC offset (`2026-11-08T00:00:00+08:00`), for
 * endpoints that require a timezone; anything else comes back unchanged so the server names the problem. */
export function localMidnightIso(value: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value.trim())
  if (!match) return value.trim()
  const [, y, m, d] = match
  const offset = -new Date(Number(y), Number(m) - 1, Number(d)).getTimezoneOffset()
  const sign = offset >= 0 ? '+' : '-'
  const hh = String(Math.floor(Math.abs(offset) / 60)).padStart(2, '0')
  const mm = String(Math.abs(offset) % 60).padStart(2, '0')
  return `${y}-${m}-${d}T00:00:00${sign}${hh}:${mm}`
}
