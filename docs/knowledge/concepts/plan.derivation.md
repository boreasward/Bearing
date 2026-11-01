---
id: plan.derivation
title: 推导与一致性
group: plan
version: 1
applies_to: [derivation-1]
summary: "只用计划 inputs 指向的已存行重算每一个价位和数字；结论分一致 / 不一致 / 不完整，「一致」只说明算术正确。"
related: [plan.ref_price, plan.sizing, indicator.atr14, system.gate_hold]
sources: [investment_assistant/derivation/chain.py, docs/features/daily-plan.md]
---

## 是什么

推导是回答「这份计划的每个数字是怎么来的」的办法：把算它用到的原始行读回来，一步步重新算一遍，再和计划里存的数比较。

## 怎么算 / 从哪来

- 用 `derivation-1`，一套**独立于生成计划代码**的实现：它自己声明常数，并有测试保证与生成时的常数一致，所以改了公式却没发新版本会让测试失败。
- 只读取计划 `inputs` 里指向的那几行（按 id 取，不会误取后来修订的新行），逐步写出公式、代入的值、结果、已存的值，以及是否一致。
- 一致性结论有三种：
  - `consistent`（一致）：每一步重算值都等于已存值；
  - `mismatch`（不一致）：有一步对不上，或触发段的某个条件被违反；
  - `incomplete`（不完整）：缺行，或计划版本不认识——缺失优先于不一致。
- 触发段只复核候选冻结的取值是否满足阈值；候选没有冻结的内容（如每日均线）标「未冻结，无法复核」。

## 在系统里哪里看到

- 「今日」计划块与没有计划的单元（只有「触发依据」）里的「展开推导」；计划页 `#plan/<id>`。批处理的 `plan`、`agent`、`review` 阶段结束前也会核对，有 `mismatch` / `incomplete` 时该阶段记 `degraded`。

## 注意

- **「推导一致」只说明算术正确，不说明规则有效**；规则与 `plan-1` 都**未经验证**（Phase 0 Gate HOLD），见[证据状态](system.gate_hold.md)。
- 数字按后端给出的字符串原样显示，前端不重算。
