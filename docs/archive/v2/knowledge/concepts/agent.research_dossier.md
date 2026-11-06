---
id: agent.research_dossier
title: 研究档案
group: agent
version: 1
applies_to: [agent-4]
summary: "一份研究请求对应一个档案，每次结论只追加版本；保存当时输出、反方、引用和截止时间，结论不是可执行计划。"
related: [agent.research_request, agent.company_researcher, system.cutoff, system.append_only, agent.counter]
sources: [investment_assistant/analysis/repo.py, investment_assistant/analysis/agent/research_check.py, investment_assistant/analysis/agent/research.py, investment_assistant/api/routes/research.py, docs/specs/2026-11-01-v2.12-v2.15-five-layers-and-agent-roles-design.md]
---

## 是什么

`research_requests` 是档案头，`research_versions` 是只追加结论链。每版保留 `agent_run_id`、cutoff、预算消耗、状态、action、summary、output 与 review_when，让你读回当时看到了什么。

## 怎么算 / 从哪来

- 展示顺序是论点、事实与来源、催化、反证与替代解释、价格与情景、下一步、复查条件；事实按 disclosure/guidance/price_fact/user/inference 分组。
- `output.research={status,reason,output}` 中的 output 保留已校验的 facts/risks/blocked/summary；档案保留已校验计划/研究输出原结构，并有独立 `counter` 字段。引用沿用原 `call:N`，不是页面重算或模型重写的版本。
- 催化、下一步等缺字段明确写「本版未记录」，不能解释为无风险或自动补出结论。版本状态是 `concluded/waiting_data/error`，请求状态与之分开。
- 本版不写 trade_plan、不自动开链；`buy/pass/wait` 都只是研究结论，plan_id 不由研究模式生成。后来出现规则信号时，新单元研究员可用 `get_research_dossier(ticker)` 读取截止前已存在的档案。
- 批处理单元的研究员段只在推理视图里，不为每次单元另建档案。

## 在系统里哪里看到

研究详情可切换真实结论版本、查看原来源和推理。历史只读已存版本；不存在的字段和关联链保持空，不用当前资料补齐过去。

## 注意

价格情景不是订单，`buy` 也不是开链授权。档案结束不抹掉旧版；研究和价格建议仍未经验证，保持 Gate HOLD。
