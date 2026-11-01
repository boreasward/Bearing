---
id: account.fill_rules
title: "模拟成交规则"
group: account
version: 1
applies_to: [paper-fill-1, entry-1, plan-shadow-2]
summary: "模拟账户复用计划影子的入场和逐日退出规则：日线成交、同日先止损、T1 卖半、移动止损与时间止损；默认无滑点。"
related: [account.paper, plan.entry_type, plan.open_scenarios, plan.max_hold, review.plan_shadow]
sources: [investment_assistant/workbench/paper_fill.py, investment_assistant/workbench/entry_rules.py, investment_assistant/workbench/plan_shadow.py, docs/specs/2026-10-31-v2.11-accounts-and-paper-trading-design.md]
---

## 是什么

`paper-fill-1` 用日线推演模拟订单和持仓，入场复用 `entry-1`，持仓逐日步进与 `plan-shadow-2` 共用一份函数。每个持仓的计划冻结在买入关联的版本，之后链的修订不会改它的价位。

## 怎么算 / 从哪来

入场区间为 [L, U]、止损为 S，订单最早在下单后的交易日结算：

| 类型 | 入场日的开盘与成交 |
| --- | --- |
| `zone_limit`、`breakout_stop` | 开在区间内按开盘成交；高于 U 则当日最低 ≤ U 时按 U 成交；低于 L 且开盘 ≤ S 作废，否则当日最高 ≥ L 时按 L 成交 |
| `next_open` | 只看首个有效日，开在区间内按开盘成交；高于 U、低于 L 或不高于止损则作废，不等以后回到区间 |

入场情况分别记 `in_zone`、`gap_up_limit`、`gap_down_stop_buy`；跳空作废原因是 `gap_below_stop`、`gap_above_zone`、`gap_below_zone`。有效期结束仍未成交则过期。

**退出顺序与价格**：

1. Agent 已有提前卖单，先按当天开盘卖全部或一半（一半按成交日股数向下取整，不足 1 股不卖），记 `agent_sell`；若只卖一半，余仓继续受自动退出约束。已有退出指令按开盘卖，记 `exit_instruction`，不会重复卖出同一股。
2. 到期的时间止损先按开盘卖，记 `time_stop`。否则当日最低 ≤ 有效止损就全部退出：开盘 ≤ 止损按开盘（`gap_stop`），其余按止损（`stop`）。**成交当天也检查；同日止损与 T1 都触及时先止损。**
3. 未到过 T1 且当日最高 ≥ T1，按 `max(开盘, T1)` 卖一半（按成交日股数向下取整；只有 1 股时不卖，但状态照常进入 T1 之后的移动止损），记 `target1_half`。收盘后更新次日止损为 max（原有效止损，开仓以来最高收盘 − 计划 ATR 倍数 × ATR，**买入成交价**）；下限不含买入费用。
4. 从成交日计第 1 天，持有达到最长交易日数，且从未收盘达到成交价 + 1R、也未到 T1，标记次一交易日开盘退出。1R = 成交价 − 冻结计划初始止损。

每笔收取账户的 `fee_per_trade`，默认 0；买入费计成本、卖出费扣所得。缺当日日线就不结算该标的，挂单与退出等待数据并记 `bars_missing`；`next_open` 缺首日数据不会拿后一天的开盘补成交。日线无法确定盘中先后，模拟时间戳只保证买卖的先进先出顺序，不是真实盘中时间；无滑点与部分成交假设。

## 在系统里哪里看到

「账户 → 成交」看入场情形、退出原因、计划版本与 `paper-fill-1`；从订单与决策记录追溯使用的日线。核对价格时同时读[开盘三种情形](plan.open_scenarios.md)与[最长持有](plan.max_hold.md)。
