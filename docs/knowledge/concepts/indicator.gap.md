---
id: indicator.gap
title: 跳空幅度
group: indicator
version: 1
applies_to: [rules-1]
summary: "开盘价相对前一日收盘价的涨跌幅；财报后第一个交易日的跳空达到 ±3% 才触发财报跳空规则。"
related: [rule.earnings_gap_up, rule.earnings_gap_down]
sources: [investment_assistant/analysis/rules.py]
---

## 是什么

跳空是开盘价和前一日收盘价之间的缺口：收盘后发了财报，次日开盘直接在另一个价位。跳空幅度越大，说明市场对消息的反应越剧烈。

## 怎么算 / 从哪来

- 跳空幅度 `gap` = 当日开盘价 ÷ 前一日收盘价 − 1，价格为拆股调整、未分红调整的开高低收。
- 只在**财报后第一个交易日**评估：由 8-K 的 Item 2.02 和受理时间决定哪一天第一次反映财报，见SEC 申报。
- 阈值固定为 3%（`gap` 阈值 0.03）：
  - 向上：`gap` ≥ +3%，且当日收盘 ≥ 开盘，见[财报跳空向上](rule.earnings_gap_up.md)；
  - 向下：`gap` ≤ −3%，且当日收盘 ≤ 开盘，见[财报跳空向下](rule.earnings_gap_down.md)。

## 在系统里哪里看到

- 财报跳空候选里冻结的 `open`、`close`、`prev_close`、`gap`，「今日」展开触发依据时逐条核对。

## 注意

- 附加的「收盘方向不反转」条件是为了排除跳空后被反向吞掉的情形。
- 受理时间缺失时算不出是哪一天，只记跳过原因 `earnings_time_unknown`，不猜；盘中发布的财报不触发跳空规则。规则**未经验证**。
