---
id: review.chain_state
title: "版本链状态"
group: review
version: 2
applies_to: [timeline-1, timeline-2]
summary: "链状态共九种，取第一条成立的：legacy、invalidated、closed_by_decision、exiting、holding、position_closed、superseded、expired、entry。"
related: [agent.version_chain, agent.review_outcome, agent.legacy, system.decision]
sources: [investment_assistant/execution/timeline.py, investment_assistant/execution/adoption.py]
---

## 是什么
链状态说明一条 Agent 计划链现在处于什么阶段，决定它还要不要被每日复查。

## 怎么算 / 从哪来
按下面的顺序检查，**取第一条成立的**（`timeline-2` 的九种；`timeline-1` 的旧复查记录按它当时的七种解释，没有 `exiting` 与 `superseded`）：

| 顺序 | 状态 | 条件 | 会被复查 |
| --- | --- | --- | --- |
| 1 | `legacy` | 是 [agent-1 旧版](agent.legacy.md)，不在链里 | 否 |
| 2 | `invalidated` | 最新版已被复查作废（入场期的作废，或 `timeline-1` 时代的任何作废） | 否 |
| 3 | `closed_by_decision` | 你 v2.9 之前最新的决定不是买（否决、降级、观察等）；只来自旧决定行 | 否 |
| 4 | `exiting` | 链上关联的买入还有未平批次，且持仓期已发出退出指令（持仓期作废） | 否（只写每日持仓状态） |
| 5 | `holding` | 链上关联的买入成交还有未平批次 | 是（持仓期） |
| 6 | `position_closed` | 链上关联的买入已全部平仓 | 否 |
| 7 | `superseded` | 入场期内，同标的在这条链仍可入场时又开了一条更新的链（只出现在 v2.9 之前的重复链上） | 否 |
| 8 | `expired` | 已到最新版截止日或入场期上限，仍没成交（当天恰有再次触发则不判过期） | 否 |
| 9 | `entry` | 其余情况：最新版还有效、尚未成交 | 是（入场期） |

只有 `entry`（入场期）和 `holding`（持仓期）两种会进入[每日复查](agent.review_outcome.md)；其余状态的链保留记录但不再复查。`entry`、`holding`、`exiting` 三种算"活跃"，同一个标的至多一条（见[再次触发](agent.signals.md)）。每个状态都附一句依据，如"第 n 版已被复查作废"。

## 在系统里哪里看到
计划页的链头部、「标的」页"Agent 计划"列表里的状态、「今日」"进行中的 Agent 计划"（只列入场期和持仓期）。

## 注意
状态按当前记录的决定与成交判定，每次读取时现算，不是写死在链上的；链状态变化不会改动已写入的版本。
