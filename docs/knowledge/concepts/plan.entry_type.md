---
id: plan.entry_type
title: "入场类型"
group: plan
version: 2
applies_to: [entry-1, plan-2, agent-3]
summary: "计划写明怎么挂单：区间限价、突破买入、次日开盘或回调买入四选一；规则基线恒用区间限价，Agent 只选类型，开盘情形的做法由系统按类型生成。"
related: [plan.open_scenarios, plan.entry_zone, plan.max_hold, plan.own]
sources: [investment_assistant/execution/entry_rules.py, investment_assistant/execution/plan_levels.py, docs/archive/v2/features/daily-plan.md]
---

## 是什么
入场区间只说"在哪个价位范围里买"，入场类型进一步说"用哪种单子买"。每份带执行字段的计划（规则基线 `plan-2`、Agent `agent-3`、v2.9 起的自定计划）都必须写明一种，口径版本 `entry-1`：

| 类型 | 代码 | 一句话 |
| --- | --- | --- |
| 区间限价 | `zone_limit` | 在入场区间内按限价买；规则基线恒用这一种 |
| 突破买入 | `breakout_stop` | 用买入止损限价单，价格升破入场下沿才成交，限价是上沿 |
| 次日开盘 | `next_open` | 只看第一个有效日，开盘价落在区间内才买，否则本计划作废 |
| 回调买入 | `pullback` | 回调到入场区间内再买；v3.2 加的第四种，研究计划与规则名单的计划都可用 |

## 怎么算 / 从哪来
- **Agent 只选类型，不写情形文字**：开盘高开、低开时怎么办，是系统按类型从固定模板生成的（见[开盘三种情形](plan.open_scenarios.md)），模板和影子模拟用同一张表，所以页面上写"作废"的格子，影子一定记作废。
- `zone_limit` 与 `breakout_stop` 在日线上的成交规则相同（区别只在你挂的单子类型），所以影子结果一致；`next_open` 只看第一个有效日（计划生效的第一个交易日）。
- v3 里入场类型没有自己的行为：v3.2 的 `pullback` 不改变任何判定（日线成交、复查、假设结果都用同一条规则），它与 `zone_limit` 的区别只是写在计划上给人看的标签。
- 类型不在这四种之内，这一版 Agent 计划作废，原因 `invalid_entry_type`（见作废与跳过原因）。
- 旧的 `agent-2` 版本没有这个字段，页面按 `zone_limit` 显示并注明"旧版计划，执行字段按默认值显示"。

## 在系统里哪里看到
「今日」计划表的"入场类型与区间"列、计划页的版本链、推送里新计划的第一行；改了类型会作为"相对上版的变化"写出（见变化说明）。

## 注意
- 系统不看盘前和盘中价格：开盘三种情形是收盘后写好的条件指令，不是盘前复核；你仍要自己挂单。
- 本版不做分批入场和追高比例（追高本来就是纪律四项之一），也**未经验证**。
