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

/** The current New York date and time (`YYYY-MM-DD` / `HH:MM`) — the defaults of the trade date/time controls.
 * Trades happen on US exchanges, so the page works in New York time whatever the browser zone is. */
export function nowInNewYork(now: Date = new Date()): { date: string; time: string } {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).formatToParts(now)
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? ''
  return { date: `${get('year')}-${get('month')}-${get('day')}`, time: `${get('hour')}:${get('minute')}` }
}

/** `${date}T${time}:00±offset` with the New York offset of that date (DST decided per date, spec §7): the offset
 * is read via `Intl`'s `timeZoneName: 'longOffset'` (`GMT-05:00` → `-05:00`). Malformed input is passed through
 * unchanged (no invented offset) so the server names the problem. */
export function newYorkIso(date: string, time: string): string {
  const d = date.trim()
  const t = time.trim()
  if (!/^\d{4}-\d{2}-\d{2}$/.test(d) || !/^\d{2}:\d{2}$/.test(t)) return `${d}T${t}`
  const [y, m, day] = d.split('-').map(Number)
  // 正午 UTC 在美东仍是同一天（7/8 点），取它当时的偏移即该日期全天的偏移（切换只发生在凌晨 2 点）
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', timeZoneName: 'longOffset' })
    .formatToParts(new Date(Date.UTC(y, m - 1, day, 12)))
  const offset = /^GMT([+-]\d{2}:\d{2})$/.exec(parts.find((p) => p.type === 'timeZoneName')?.value ?? '')
  return offset ? `${d}T${t}:00${offset[1]}` : `${d}T${t}`
}

/** Two decimals with the `$` prefix (`-$12.34` when negative); empty stays empty, unparseable text is shown as it was. */
export function moneyText(raw: string | number | null | undefined): string {
  if (raw === null || raw === undefined || raw === '') return ''
  const n = Number(raw)
  if (!Number.isFinite(n)) return String(raw)
  const fixed = Math.abs(n).toFixed(2)
  return n < 0 && Number(fixed) !== 0 ? `-$${fixed}` : `$${fixed}`
}
