---
id: review.process_check
title: "流程复查触发"
group: review
version: 1
applies_to: [monthly-3, monthly-4]
summary: "连续 3 个月的复合收益为负，且低于同期 SPY 的复合收益，才标记需要复查流程；缺任一个月的数据则不判定。"
related: [review.monthly_return, review.sensitivity, review.tracks]
sources: [investment_assistant/review/monthly.py]
---

## 是什么
单月收益波动大，不适合据此改变做法。流程复查是一道更严格的闸门：只有连续三个月既亏钱、又跑输大盘，才提醒你坐下来检查整套流程（规则、计划、执行），而不是调整某个参数。

## 怎么算 / 从哪来
取最近连续三个月，分别计算你的复合收益和同期 SPY 的复合收益（逐月 (1 + 月收益) 连乘再减 1）。同时满足两条才标记：

1. 你的三个月复合收益小于 0；
2. 它还小于 SPY 的三个月复合收益。

结果有三种：需要复查（`review`）、正常（`ok`）、数据不足（`insufficient`，不足三个月或任一个月缺收益或缺 SPY 数据）。数据不足时不会假造数字，复合收益留空。标记时附逐月明细。

## 在系统里哪里看到
「复盘 → 月度复盘」卡片上的流程复查提示与三个月的逐月明细。

## 注意
这是"提醒你去检查"，不是结论：它不会自动改任何规则或阈值，系统的规则本来就是事先固定、不用历史数据调参的。
