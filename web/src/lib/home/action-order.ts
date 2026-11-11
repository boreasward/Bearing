// 今日操作的排序（v3.1 spec §7.4 + v3.3 spec §8）：止损触及未卖出最紧急，其后依次下次开盘卖出、换仓卖出、缺日线、止损单、持有。
// 今日页（TodayActions）与仪表面板（DashboardPane）共用这一份。
export const ACTION_ORDER: Record<string, number> = {
  stop_hit_unresolved: 0,
  sell_next_open: 1,
  rotation_sell: 2,
  unchecked: 3,
  stop_order: 4,
  hold: 5,
}

export function sortActions<T extends { kind: string }>(actions: readonly T[]): T[] {
  return [...actions].sort((a, b) => (ACTION_ORDER[a.kind] ?? 9) - (ACTION_ORDER[b.kind] ?? 9))
}
