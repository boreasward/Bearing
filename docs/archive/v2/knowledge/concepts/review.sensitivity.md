---
id: review.sensitivity
title: "风险敏感性表"
group: review
version: 1
applies_to: [monthly-3, monthly-4]
summary: "用当月已平仓交易的实际 R，按单笔风险 0.5%、1%、1.5%、2% 重算近似月度收益和最大回撤，看仓位大小对结果的影响。"
related: [review.r_multiple, review.monthly_return, plan.sizing]
sources: [investment_assistant/review/monthly.py]
---

## 是什么
同样的一串交易结果，每笔冒多大的风险，月度收益和回撤会差很多。敏感性表把当月已平仓交易的实际 [R](review.r_multiple.md) 按四档单笔风险重新折算，让你看到如果仓位更大或更小会是什么样子。

## 怎么算 / 从哪来
- 取当月已平仓交易的实际 R，按平仓时间排序。
- 对风险比例 0.5%、1%、1.5%、2% 各算一行：近似收益 = 全部 R 之和 × 风险比例；最大回撤 = 累计 R 曲线从峰值下滑最深的幅度 × 风险比例。
- 表里注明用了几笔交易。

这是近似：没有考虑复利和仓位上限，也没有计入未平仓的部分。你实际的单笔风险由[建议仓位](../../../../knowledge/concepts/plan.sizing.md)里的风险比例决定。

## 在系统里哪里看到
「复盘 → 月度复盘」卡片里的敏感性表。

## 注意
交易笔数很少时，这张表几乎没有信息量；它回答的是"仓位大小的影响"，不是"系统是否有效"。
