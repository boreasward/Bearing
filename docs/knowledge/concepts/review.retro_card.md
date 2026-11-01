---
id: review.retro_card
title: "复盘卡"
group: review
version: 2
applies_to: [retro-1, agent-retro-1, retro-2, agent-retro-2]
summary: "交易平仓或你写过备注的计划结束后，系统冻结当时的事实，Agent 只讲述并起草教训草稿，由你审定一次；教训进入我的知识。v2.9 起卡讲的是你的执行而不是决定。"
related: [review.retro_end, system.my_knowledge, review.tracks, review.plan_shadow, system.append_only, review.discipline_early_take]
sources: [investment_assistant/review/retro.py, investment_assistant/review/knowledge/items.py]
---

## 是什么
复盘卡把"一笔交易或一份你写过备注的计划是怎么结束的"固定下来，对照当初的预期和后来的实际，再把你的执行和纪律放在一起看。它不是计划，也不改任何已有记录。

## 怎么算 / 从哪来
- **对象有两类**（`retro-2`，v2.9）：一类是链或单元——有关联买入成交的（成交关联的是被并入单元的规则基线也算这条链），或者你给它写过备注而没有成交的；另一类是计划外成交（没有关联计划的买入成交，每笔一张）。没有成交、也没写过备注的链不出卡。`retro-1` 的对象是「做过决定」的单元，已有的旧卡原样保留。什么算"结束"见[结束事件](review.retro_end.md)。
- **事实由程序冻结**：检测到结束事件时，程序一次算好事实并写进卡里，之后不重算（`retro-1` 十个，`retro-2` 十一个：`decision` 换成 `execution`，另加 `holding`）：`subject`（对象与结束）、`plans`（各份计划当时的价位、有效期与角色）、`bars_after`（之后的日线摘要与首次触及日期；各计划的止损、T1 触及日从首次进入进场区间那天起往后找，价位与[影子结果](review.plan_shadow.md)同样按拆股还原后的口径）、`shadow`（各计划的[影子结果](review.plan_shadow.md)）、`tracks`（[三条轨道](review.tracks.md)的 R）、`decision`（你的决定，`retro-1`）、`execution`（你的执行：状态、照什么执行、当时的备注，旧决定照录在 `legacy_decision`，`retro-2`）、`holding`（持仓期每日状态摘要：持有天数、退出指令的日期与实际卖出日，`retro-2`）、`trades`（成交与实际 R）、`discipline`（纪律四项，含待定）、`reviews`（链的复查摘要）、`knowledge`（计划当时引用过的我的知识）。
- **Agent 只写叙述和教训草稿**：数字不能改写、不能另算，叙述的每一句都要引用上面的事实或工具调用；教训必须写适用范围和反例。
- **状态由记录推出、不另存**，共四种：`reviewed`（已审定）、`to_review`（待审定）、`draft_failed`（起草失败，可手动审定或重新起草，尝试达 3 次）、`drafting`（起草中）。
- **审定只能一次**：你逐条采纳（可改）或不采纳教训，也可以另写一条、写一段"我的复盘"；采纳的教训才成为[我的知识](system.my_knowledge.md)的第 1 版。之后要改经验，到我的知识里出新版本。

## 在系统里哪里看到
「复盘」页的"复盘卡"页签：列表按结束日倒序，点开是卡页（预期与实际、三条轨道、你的执行（旧卡是「你的决定」）与[纪律](review.discipline_early_take.md)、教训草稿与审定、起草过程）。

## 注意
卡上的纪律是检测当时的结果，过早止盈、死扛亏损要等一段时间才能判定，没到期的写"待定"；卡页另显示"现在的判定"。教训草稿没有被审定前，任何 Agent 都读不到。所有规则、Agent 和教训都未经验证，卡只是帮你对照记录，不证明哪个做法有效。
