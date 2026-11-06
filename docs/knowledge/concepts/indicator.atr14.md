---
id: indicator.atr14
title: ATR14
group: indicator
version: 2
applies_to: [indicator-1, plan-2, plan-1, screen-1]
summary: "最近 14 个真实波幅的平均，衡量一只股票一天通常波动多少；止损、目标、入场区间和移动止损都以它为尺。"
related: [indicator.registry, plan.stop, plan.target1, plan.entry_zone, plan.trailing_stop]
sources: [investment_assistant/derivation/indicators/registry.py, investment_assistant/derivation/indicators/_price_math.py, investment_assistant/derivation/indicators/price.py]
---

## 是什么

ATR14 是「14 日平均真实波幅」。它告诉你这只股票一天的典型波动幅度（以美元计）：波动大的股票 ATR 大，止损就要放得更远才不会被日常波动扫掉。`plan-1` 不用固定百分比，而是用 ATR 的倍数定所有价位，让不同波动的股票用同一把尺。

**原数据族 ATR** 已并入[指标注册表](indicator.registry.md)：由日线现算、不入库、不单独拉取；更新其输入仍是拉取日线。plan-2 沿用原数值，不因归类变化重算旧计划。

## 怎么算 / 从哪来

- 某一天的**真实波幅**（TR）取三者中最大的：当日最高 − 最低、|当日最高 − 前收盘|、|当日最低 − 前收盘|。这样隔夜跳空也算进波动。
- ATR14 按 Wilder 方法：第一个值是 14 个 TR 的平均，之后用（前值 × 13 + 当日 TR）÷ 14 平滑。系统只取最近 15 根日线（得到 14 个 TR），因此实际等于最近 14 个真实波幅的简单平均。
- 价格用开高低收（拆股调整、未分红调整），见日线。不足 15 根日线算不出来，显示为空。
- 计划里的 ATR 在生成时冻结，保留到小数点后 6 位。
- 注册项 `atr14` 的 basis=high_low、window=15；`atr_pct` 也是15根，值为 ATR14 / **close**，不是 adj_close。覆盖须区分样本不足与截止日缺日线；不足15根时计划记 `atr_unavailable`。

## 在系统里哪里看到

- 规则计划的各价位都以它为尺：[入场区间](plan.entry_zone.md)（参考价 ± 0.5 ATR）、[止损](plan.stop.md)（参考价 − 2 ATR）、[目标 T1](plan.target1.md)（参考价 + 4 ATR）、[移动止损](plan.trailing_stop.md)（最高收盘 − 3 ATR）。
- 每日筛选要求 ATR14 ÷ 收盘价落在 2%–6% 之间才进入形态组。
- Agent 的 `get_indicators` 仍返回原 `atr14`；「标的」页关键数据与「数据 → 指标」中的 ATR 注册项也可核对。

## 注意

- ATR 只描述过去的波动，不预测未来；跳空超过止损时，实际亏损会大于 2 ATR。
- 它是 `plan-1` 的计算输入，而 `plan-1` 的规则**未经验证**（Phase 0 Gate HOLD）。
