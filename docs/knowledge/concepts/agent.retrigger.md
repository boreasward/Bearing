---
id: agent.retrigger
title: "再次触发"
group: agent
version: 1
applies_to: [timeline-2, agent-3, agent-review-2]
summary: "同一标的又触发买入规则时，不开第二条链，而是并入已有的活跃链：入场期直接修订并顺延有效期，持仓期只当证据，退出期只记录。"
related: [system.active_chain, agent.signals, agent.changes, plan.validity, review.holding_status]
sources: [investment_assistant/analysis/units.py, investment_assistant/analysis/agent/runner.py, investment_assistant/execution/timeline.py, docs/features/agent-track.md]
---

## 是什么
一只股票在已有活跃计划链的时候，又在新的信号日触发了买入规则（例如连续两天都出买入候选）。v2.9 之前这会各自开一条链；现在每只股票同一时间只有一条活跃链（见[一只股票一条活跃链](system.active_chain.md)），新的触发作为"再次触发"**并入**那条链。

## 怎么算 / 从哪来
- **谁判定**：`plan` 阶段为无冲突的买入单元生成规则基线之后，若该标的已有活跃链，就写一行 `chain_retriggers`（被并入的单元、并入的链、当时的链状态、本单元的规则、规则基线计划、入场期上限）。这是宿主判定，不依赖模型，Agent 不可用时也照样落库；同一次运行重跑不重复写。入场期最后一个有效日（或入场期上限那天）触发，算可续期的活跃链，续期而不是新开。
- **`agent` 阶段**：有并入行的单元不提议、不写运行行，阶段跳过原因 `active_chain`，解读用规则版简报。
- **复查**：第八项信号 `retrigger` 取 `chain_retriggers.created_at` 落在窗口内的行（见[系统信号](agent.signals.md)）。
  - **入场期**：跳过单阶段复查，直接走修订流程（提示词带这次触发的规则、冻结取值与规则基线价位）。价位和条件都不变时允许只**续期**：新版 `changes = []`，宿主差异只有有效期 `valid_until` 与入场期上限 `entry_cap`；入场期上限改为**最近一次触发后第 10 个交易日**（否则第 9 天再触发的链只剩 1 天入场期）。修订输出 `pass` 仍作废，失效条件成立仍先由宿主作废。
  - **持仓期**：只是证据，进单阶段复查的证据块；不加仓，止损只能上移。
  - **退出期**：只记进当天持仓状态的信号，不调用模型。
- 页面与推送用宿主模板写变化说明："再次触发（<规则>，<信号日>），沿用价位，有效期延至 <日期>"。

## 在系统里哪里看到
计划表链所在行下的"10-30 再次触发 → 第 1 版"；计划页复查记录的"再次触发（…）→ 直接修订"；「标的」页计划历史的链内一行；运行详情 `plan` 节点的 `retriggers` 计数。

## 注意
- 并入的链当天被宿主作废（失效条件成立）时，这次触发只剩规则基线，不补开新链，要等下一次触发。
- 备注和"我的补充"是两回事：只有"我的补充"会进 Agent 提示词。规则**未经验证**。
