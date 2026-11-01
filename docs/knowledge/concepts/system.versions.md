---
id: system.versions
title: "版本号"
group: system
version: 4
applies_to: [plan-2, plan-shadow-2, agent-3, timeline-2, condition-2, entry-1, execution-1, discipline-2, tracks-2, retro-2, agent-retro-2, review-8, monthly-4]
summary: "plan-1、agent-2 这类标签标识每个口径的版本：改口径就发新版本，旧记录保留当时的版本，所以新旧口径的结果不会被悄悄混在一起。"
related: [system.concept_card, system.append_only, system.stages, agent.version_chain, system.my_knowledge]
sources: [investment_assistant/execution/plan_levels.py, investment_assistant/execution/entry_rules.py, investment_assistant/execution/adoption.py, investment_assistant/review/discipline.py, investment_assistant/review/tracks.py, investment_assistant/analysis/rules.py, investment_assistant/execution/timeline.py, investment_assistant/core/stages.py, investment_assistant/workflow/graph.py, investment_assistant/review/retro.py]
---

## 是什么
系统里每套计算口径都有一个版本号，写在它产生的每一条记录上。规则：**改口径（阈值、公式、流程）就发新版本；旧记录保留原版本，不重算。**这样你读到一条旧记录，总能知道它是按哪套规矩算出来的。

## 怎么算 / 从哪来
当前版本标签与含义（括号里是它标识的东西）：

- `rules-1`：规则 v1 的八条规则与阈值（另两条持仓卖出规则同属此版）
- `plan-1`：规则计划的公式；`context-2`：计划里冻结的市场环境
- `screen-1`：每日筛选；`regime-2`：区制判定（`regime-1` 是旧口径）
- `agent-2`：带论点、失效条件、版本链的 Agent 计划（`agent-1` 是旧版，见 [agent-1 旧版](agent.legacy.md)）；`agent-review-1`：每日复查；`agent-brief-1`：解读
- `condition-1`：失效与复查条件登记表；`timeline-1`：版本链、信号与链状态
- `derivation-1`：计划推导与一致性核对
- `plan-shadow-1`：计划影子；`tracks-1`：三方对比；`review-7`：周度复盘（新增"你学到了什么"，`review-6` 是旧口径）；`monthly-3`：月度复盘；`backcheck-2`：上线前检查
- `stages-4`：十个节点的运行证据（`sec_monitor` 在前、`retro` 在 `push` 之后，续跑追加 `attempt`；`stages-3` 是九个阶段、`stages-2` 是八个、`stages-1` 是七个的旧口径）
- `daily-1`：每日工作流这张图的节点、边、状态形状与续跑规则；`fetch-1`：外部请求策略（连接 10 秒、总 30 秒、重试 2 次）；`siwc-1`：ChatGPT 连接的 OAuth 与请求改写规则
- `retro-1`：复盘卡的对象、结束事件判定、冻结事实与决定文案（v2.9 起是旧卡口径，`retro-2` 取代它）；`agent-retro-1`：复盘卡起草（同样被 `agent-retro-2` 取代）
- `my-knowledge-1`：我的知识的字段、挂靠、版本与截止时间可见性；`knowledge-perf-1`：知识表现回看的样本口径；`agent-discuss-1`：就此计划讨论的回答
- `knowledge-1`：概念卡与原理页的格式与加载规则
- v2.9「可执行的计划」新增：`plan-2`（规则计划加入场类型与最长持有）、`plan-shadow-2`（影子按开盘情形与时间止损回放，`plan-shadow-1` 作反事实对照）、`agent-3`（Agent 计划加执行字段）、`timeline-2`（九种链状态、每标的一条活跃链、再次触发）、`condition-2`（加 `held_sessions_reached`）、`entry-1`（入场类型与开盘三种情形）、`execution-1`（由成交推出采纳）、`discipline-2`、`tracks-2`、`retro-2`；随之升版的还有 `agent-review-2`、`agent-retro-2`、`review-8`、`monthly-4`。`discipline-1` 指 v2.8 及以前的纪律口径。这些版本随各自的功能分步启用，页面上记录带着哪个版本就按哪个解释

概念卡自己也有版本，是另一回事：见[概念卡](system.concept_card.md)。

## 在系统里哪里看到
计划、候选、复盘快照、阶段行上都带着版本号；就地展开的概念摘要里显示它适用的版本。

## 注意
版本号相同才可以直接比较。旧版本的行不会因为口径更新而改变，这是有意的。
