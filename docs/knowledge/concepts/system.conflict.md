---
id: system.conflict
title: "冲突与回避"
group: system
version: 1
applies_to: [rules-1]
summary: "同一天既有买入又有卖出候选标记为冲突并默认观察；没有持仓却出现卖出候选标记为回避。"
related: [system.decision_unit, rule.trend_break, rule.earnings_gap_down, agent.brief]
sources: [investment_assistant/analysis/synthesis.py]
---

## 是什么
两种需要你多看一眼的标记，都属于[决策单元](system.decision_unit.md)。

## 怎么算 / 从哪来
- **冲突**：同一股票同一天同时出现买入候选和卖出候选。例如价格重新站上均线的同一天却放出了向下的财报跳空。系统不替你裁决，建议动作默认为观察，并且不为它生成规则计划；Agent 会单独写一份[解读](agent.brief.md)，列出支持和反证。
- **回避**：只有卖出候选，而你**没有持仓**。卖出规则本是为持仓设计的，没有持仓时它只说明"这只股票出现了走弱信号"，所以标记为回避，提醒你别去买它，而不是叫你卖。

## 在系统里哪里看到
「今日」决策单元卡上的"冲突"或"回避"标签；解读里的"冲突"栏会写出互相矛盾之处。

## 注意
冲突的默认处理是观察，你可以在决定里采纳别的动作，但要写下理由；标记本身不代表哪一边对。
