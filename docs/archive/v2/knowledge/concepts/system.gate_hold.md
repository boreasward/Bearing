---
id: system.gate_hold
title: "证据状态「未验证」"
group: system
version: 1
applies_to: []
summary: "Phase 0 Gate 的结论是 HOLD：所有规则和 Agent 都未经验证；推导一致、Agent 默认展示、数据完整都不等于有效。"
related: [system.cutoff, review.backcheck, review.tracks, plan.derivation]
sources: [investment_assistant/data/families.py, investment_assistant/derivation/chain.py, investment_assistant/review/push_digest.py]
---

## 是什么
这个系统出的是**研究候选和计划**：它告诉你哪些股票触发了规则、规则会给出怎样的价位、Agent 怎么看，由你决定、你自己下单；系统不接券商，也不自动交易。更重要的是，这些规则和 Agent 到目前为止**没有被验证过有预测价值**。Phase 0 事件研究的正式 Gate 结论是 HOLD（暂不放行），所以系统里每一处价位和信号都带着"证据：未验证"。

## 怎么算 / 从哪来
这不是一个计算，而是一条贯穿全系统的读法。几个容易误读的地方：

- **推导一致 ≠ 规则有效**：「展开推导」只说明价位的算术和输入对得上，不说明规则本身赚钱。
- **Agent 默认展示在前 ≠ Agent 有效**：Agent 计划成为「今日」和推送的默认复查对象，是产品选择；规则计划继续计算、影子跟踪、三方对比，不因界面默认展示就解除 HOLD，也不设自动晋升。
- **数据完整 ≠ 信号有效**：「数据」菜单只展示采集事实。
- **回看检查与影子 ≠ 证明**：上线前检查、计划影子、对照组都是描述性统计，样本小、信号同日重叠多，不宣称任何规则或 Agent 有效。
- 价位是你所设规则（或模型）的算术结果，不是投资建议。

## 在系统里哪里看到
「数据」页顶部的证据说明、推导视图里常驻的说明、「今日」每份计划的"证据：未验证"、每日推送的每条计划、复盘各视图底部的"描述性统计"提示。

## 注意
"未验证"不是"无效"，只是没有证据能证明有效。请把系统当作一份结构化的研究备忘录，而不是信号服务。
