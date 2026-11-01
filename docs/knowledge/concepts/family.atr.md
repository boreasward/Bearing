---
id: family.atr
title: ATR（派生）
group: family
version: 1
applies_to: [plan-1]
summary: "由日线现算、不入库的波动尺子，与 plan-1 用同一个函数；它是止损、目标和仓位的计算输入。"
related: [indicator.atr14, family.bars, plan.stop, plan.sizing]
sources: [investment_assistant/data/families.py, investment_assistant/derivation/indicators/price.py, docs/features/data-menu.md]
---

## 是什么

ATR 是「平均真实波幅」，衡量一只股票一天通常波动多少。它不是采集来的数据，而是从[日线](family.bars.md)**现算**的派生数据，所以数据菜单里它没有自己的拉取，要更新就「拉取日线」。

## 怎么算 / 从哪来

- 与 `plan-1` 生成计划用的是同一个函数 `plans.atr14`，算法见 [ATR14](indicator.atr14.md)。
- 现算、不入库：读取时按日线逐日算出，「数据」菜单里浏览最近 60 个值，显示 `close`、`atr`、`atr_pct`。
- 「覆盖」的口径：至少有 15 根日线、且最新一根等于最近已收盘交易日的标的数。

## 在系统里哪里看到

- 「数据」菜单的「ATR」卡与浏览区，页面注明「现算，与 `plan-1` 同一函数；不入库」。
- 每份规则计划的推导视图里，ATR 是第一项计算；它还用作每日筛选的波动率区间。

## 注意

- 不足 15 根日线算不出 ATR，此时规则计划不生成，记跳过原因 `atr_unavailable`。
- 证据说明：ATR 是 `plan-1` 的计算输入，而 `plan-1` 的规则**未经验证**（Phase 0 Gate HOLD）；数字对得上只说明算术没错。
