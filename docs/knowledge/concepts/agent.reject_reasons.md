---
id: agent.reject_reasons
title: "作废与跳过原因"
group: agent
version: 1
applies_to: [agent-3, agent-2, agent-1, agent-review-2, agent-review-1, agent-retro-1, agent-discuss-1]
summary: "Agent 一份建议为什么被作废、跳过或出错，共 55 个原因代码，每个有固定的中文文案；被作废的建议只显示原因、不显示内容。"
related: [agent.action_levels, agent.citation, agent.review_outcome, agent.invalidation]
sources: [investment_assistant/analysis/agent/__init__.py, investment_assistant/core/llm/model.py, investment_assistant/analysis/agent/runner.py]
---

## 是什么
Agent 运行的状态有五种：`ok`（通过）、`pass`（不建议买）、`rejected`（校验作废）、`error`（出错）、`skipped`（没有运行）。后三种都带一个原因代码，页面上显示对应的中文文案。作废（`rejected`）的建议**只显示原因，不显示内容**，因为内容没通过校验，不能当作建议看待。

## 怎么算 / 从哪来
原因代码与文案由系统固定，下表是全部 55 个：

| 代码 | 文案 | 一般出现的情形 |
| --- | --- | --- |
| `disabled` | 已关闭 | Agent 被配置为关闭（`WORKBENCH_AGENT_PROVIDER=off`），整个轨道跳过 |
| `not_configured` | 未配置 | 配置项取值无效，或 ChatGPT 已登录但这个账号没有开启计划用量共享 |
| `quota_exhausted` | 额度用尽 | ChatGPT 订阅的共享用量达到上限（错误码 `subscription_sharing_usage_limit_exceeded`），其余单元跳过 |
| `token_budget` | 超出 token 上限 | 达到每日 token 上限 |
| `stage_budget` | 超出时限 | 批处理开始 1500 秒后不再启动新单元 |
| `max_attempts` | 已达重试上限 | 同一单元尝试已达上限 |
| `malformed` | 输出格式无效 | 输出不是合法 JSON，或字段缺失、类型不对 |
| `invalid_citation` | 引用无效已作废 | 引用不存在，或价位缺少价格类工具支撑 |
| `level_check` | 价位校验未通过 | 价位顺序不对，或超出最近收盘价 ±50% |
| `tool_rounds_exceeded` | 工具调用超限 | 工具调用轮数超过上限 |
| `model_error` | 模型调用失败 | ChatGPT 服务出错、网络中断、超时、回答没有完成，或请求被拒绝（带安全的错误码，不含请求内容） |
| `checkpoint_unavailable` | 状态库未就绪 | 运行状态存储不可用 |
| `thesis_count` | 论点数量不符 | 论点不在 1 到 3 条 |
| `invalid_condition` | 失效或复查条件无效 | `kind` 未登记、不能用在该栏、参数不合法或缺引用 |
| `changes_mismatch` | 变化说明与实际差异不符 | 修订的说明字段与系统算出的差异不一致 |
| `no_change` | 修订与上版没有差异 | 修订版和上一版完全相同 |
| `review_malformed` | 复查输出格式无效 | 复查结论的格式不对 |
| `migration_026_missing` | 迁移 026 未执行 | 库里缺版本链所需的表 |
| `review_in_progress` | 正在复查 | 这条链正被另一次复查占用 |
| `plan_invalid` | 修订版按规则作废 | 入场期的修订版按计划规则作废（如有效期内有财报） |
| `retro_malformed` | 复盘卡草稿格式无效 | 复盘卡起草的输出不是合法 JSON，或字段、长度、条数不符 |
| `lesson_invalid` | 教训草稿缺适用范围、反例或挂靠无效 | 教训的适用范围或反例为空，概念不存在、标的无效或挂靠超过 10 处 |
| `answer_malformed` | 回答格式无效 | 讨论回答不是合法 JSON，或回答、引用的字段不符 |
| `migration_027_missing` | 迁移 027 未执行 | 库里缺复盘卡、我的知识与讨论所需的表 |
| `discussion_busy` | 上一个问题还在回答 | 同一次讨论里上一轮还没有回答，不能再问 |
| `draft_exists` | 这张卡已有成功的草稿 | 这张复盘卡已经有一份成功的草稿，不用重新起草 |
| `not_connected` | ChatGPT 未连接 | 还没有连接 ChatGPT，连接已失效需要重新登录（刷新令牌过期、被撤销、重复使用，或账号不一致），或模型服务判定当前凭据 / 授权不可用；整个节点跳过 |
| `model_unavailable` | 所选模型不可用 | 还没有选模型，或所选模型已不在 ChatGPT 提供的清单里（`model_not_found`）；整个节点跳过，到「设置 → Agent 连接」重新选择 |
| `invalid_entry_type` | 入场类型不在允许的三种之内 | `agent-3` 的 `entry_type` 不是区间限价 / 突破买入 / 次日开盘之一，见[入场类型](plan.entry_type.md) |
| `invalid_max_hold` | 最长持有须为 5–20 个交易日 | `agent-3` 的 `max_hold_sessions` 越界或不是整数，见[最长持有](plan.max_hold.md) |
| `stop_lowered` | 持仓期不能下移止损 | 持仓期的修订把止损定得比上一版低 |
| `stop_above_close` | 持仓期止损不能高于最新收盘 | 持仓期的修订把止损定在复查日收盘价或以上 |
| `active_chain` | 已有进行中的计划链，这次触发已并入 | 这只股票已有活跃链，新触发并入它而不是另开（`agent` 阶段的跳过原因，也是手动重新运行的 409），见[再次触发](agent.retrigger.md) |
| `research_malformed` | 研究输出格式无效 | 研究员输出不是合法 JSON，或字段不是 facts / risks / blocked / summary 四项、类型不对 |
| `research_facts_count` | 研究事实数量不符 | facts 不在 1 到 12 条 |
| `research_kind` | 研究事实种类无效 | 事实的 `kind` 不在登记的种类里 |
| `research_inference_unsupported` | 研究推断缺少事实支持 | 推断没有引用，或推断超过 3 条，或推断与非推断事实没有共同引用 |
| `research_refs_invalid` | 研究引用无效 | 引用不是本次研究阶段真实存在的工具调用 |
| `research_summary_length` | 研究摘要长度不符 | 摘要为空或超过 300 字 |
| `research_blocked_shape` | 研究缺失数据说明无效 | blocked 超过 6 条，或某条不是"缺什么 / 为什么"两个文本字段 |
| `pm_malformed` | 组合评论格式无效 | 组合经理输出不是合法 JSON，或字段、类型不对 |
| `pm_summary_length` | 组合摘要长度不符 | 摘要为空或超过 300 字 |
| `pm_levels_forbidden` | 组合评论不得修改价位或股数 | 评论里出现了价位或股数字段；组合经理只给意见，不改数字 |
| `pm_ranking_invalid` | 组合排序无效 | 排序引用了不可执行或重复的计划，或名次不是不重复的正整数 |
| `pm_refs_invalid` | 组合评论引用无效 | 权衡的引用不是本次真实工具调用，也不是组合检查事实 |
| `proposal_malformed` | 提案格式无效 | 提案输出不是合法 JSON，或某条的字段缺失、类型不对 |
| `proposal_source` | 提案来源无效 | 来源不是 data / agent_quality / route 之一 |
| `proposal_title` | 提案标题为空 | 标题为空 |
| `proposal_length` | 提案字段长度不符 | 改什么 / 理由 / 预期 / 验收超出长度上限 |
| `proposal_refs_invalid` | 提案证据引用无效 | 引用不在这一轮冻结的证据包里 |
| `proposal_duplicate_title` | 提案标题重复 | 与已有未决提案或本轮前面的提案同名 |
| `proposal_limit` | 超出每次三条提案上限 | 一轮最多留三条有效提案，其余丢弃 |
| `brief_empty` | 实施说明为空 | 实施说明为空 |
| `brief_length` | 实施说明过长 | 实施说明超过 20000 字 |
| `brief_acceptance_missing` | 实施说明缺少验收小节 | 没有非空的验收小节 |

大致规律：校验类原因（`malformed` 到 `no_change` 一带，`invalid_entry_type`、`invalid_max_hold`、`stop_lowered`、`stop_above_close`，以及 `retro_malformed`、`lesson_invalid`、`answer_malformed`）对应 `rejected`；`migration_027_missing`、`discussion_busy`、`draft_exists` 是复盘卡与讨论的前置检查拒绝的原因，不生成 Agent 运行；预算和可用性类（`active_chain`、`disabled`、`quota_exhausted`、`token_budget`、`stage_budget`、`max_attempts`、`not_connected`、`model_unavailable` 等）对应 `skipped`；`model_error`、`checkpoint_unavailable` 对应 `error`。

新增的 `research_*`、`pm_*`、`proposal_*`、`brief_*` 都是校验类：研究段、组合评论或实施说明整体未通过时只显示原因、不显示内容；提案则逐条校验，一条不合格只丢这一条，不连累同批其它提案，丢弃原因记在该轮运行里。

## 在系统里哪里看到
「今日」Agent 块的"Agent 未给出计划：原因"；复盘卡页的起草失败原因与讨论里"这次回答没有通过引用校验"；计划页复查记录里的跳过、出错和"修订未通过"的原因；「运行一览」阶段行的跳过原因。

## 注意
作废不等于模型错了：很多是格式或预算问题。同一个单元一天内可能因不同原因多次尝试，最终展示的是最后一次的结果。
