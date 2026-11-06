---
id: system.active_chain
title: "一只股票一条活跃链"
group: system
version: 1
applies_to: [timeline-2]
summary: "入场期、持仓期、退出期的链都算活跃，同一只股票同一时间至多一条；新触发并入它，不再另开；上线前的重复旧链判为被取代。"
related: [agent.retrigger, review.chain_state, review.holding_status, agent.version_chain, system.append_only]
sources: [investment_assistant/execution/timeline.py, investment_assistant/execution/chains.py, docs/archive/v2/features/agent-track.md]
---

## 是什么
一条 Agent 计划链是一个标的从开出到结束的全部版本。"活跃"指它还在起作用：`entry`（入场期，等成交）、`holding`（持仓期）、`exiting`（退出期：已发出退出指令，但链上的买入还没卖完）。v2.9 起**每只股票同一时间至多一条活跃链**；它再次触发买入规则时并入这条链（见[再次触发](agent.retrigger.md)），不再另开。

## 怎么算 / 从哪来
- 状态每次现算，不存状态列（见[版本链状态](review.chain_state.md)）：活跃 = `entry` / `holding` / `exiting`；复查的对象只有 `entry` 与 `holding`，退出期不复查。
- **互斥怎么保证**：`plan` 阶段判定并写并入记录时持事务级锁 `chain-open:<标的>`；`agent` 阶段为某标的写首版之前在同一把锁下再核对一次，期间已有活跃链就改记 `active_chain` 跳过；手动"重新运行 Agent"遇活跃链返回 409 `active_chain`（所并入的链已结束的除外）。
- **旧数据**：上线前可能已有同标的两条入场期的链。`timeline-2` 把更旧的那条判为 `superseded`（被取代，终止）：只在新链的信号日那条链仍处于入场期（没过期、没成交）时才成立，所以只会出现在 v2.9 之前的重复链上；两条都在持仓的旧链各自保持持仓。不写任何合并记录。
- **每天核对**：`plan_review check` 对首版为 `agent-3` 的链核对"每个标的至多一条活跃链"，并核对它与仍活跃的旧链不重叠。

## 在系统里哪里看到
「今日」计划表每个标的一行；「标的」页的计划历史；运行详情里 `plan` 节点的"并入已有活跃链 n 个"。

## 注意
- 没有 Agent 链的持仓（成交只关联规则基线）不参与复查，只有宿主算的持仓状态；之后同标的再触发会开一条入场期的新链，与持仓并存（链与持仓不自动合并）。
- `superseded` 只会出现在 v2.9 之前的重复旧链上：上线后新链只在同标的没有活跃链时才开。
