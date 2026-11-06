---
id: agent.company_researcher
title: 公司研究员
group: agent
version: 1
applies_to: [agent-4]
summary: "首版提议前先整理基本面与披露事实，不写价位；引用校验后交给提议者和反方，失败仅标缺研究，不阻断单元。"
related: [agent.action_levels, agent.research_dossier, family.fundamentals, family.sec_text, system.data_request]
sources: [investment_assistant/analysis/agent/research_check.py, investment_assistant/analysis/agent/research.py, investment_assistant/analysis/agent/toolsets.py, docs/specs/2026-11-01-v2.12-v2.15-five-layers-and-agent-roles-design.md]
---

## 是什么

`agent-4` 的首版先跑 `research`，再跑提议、校验、反方；研究员只看基本面与披露，不给买卖建议，不读价格工具、不写价位。运行版本升为 `agent-4`，计划 schema 仍是 `agent-3`。

## 怎么算 / 从哪来

- 白名单为 `get_fundamentals`、`get_guidance`、`get_filings`、`get_filing_facts`、`get_filing_text`、`get_sec_hits`、`get_earnings`、`get_news`、`get_concept`、`get_knowledge`、`request_data`、`get_research_dossier`；读源受截止时间与标的范围约束。概念卡随代码，不按截止时间回读。
- 输出四块：`facts` 1–12条、`risks` 至多6条、`blocked` 至多6条、`summary` 至多300字。事实/风险每条至多200字，缺什么与工具原因写入 blocked，不写成「不存在」。
- 事实种类 `disclosure/guidance/price_fact/user/inference` 是固定 schema；不因此赋予研究员价格工具。inference 至多3条，必须至少共享一个本输出中非推断事实的有效引用。
- 引用只认本研究阶段真实 `call:N`。校验通过后形成 `[fact:research]`，连同原编号给提议者与反方；成功研究的首版论点至少一条引用研究阶段，研究非 ok 时豁免。
- 研究仅在首版运行（含研究模式）；复查修订、再触发、补查重跑沿用最近研究段，状态 `carried`，不重新研究。其它状态为 `ok/rejected/error/disabled`，保留 reason/output；失败或关闭不阻断提议者，token 仍计预算。

## 在系统里哪里看到

推理阅读在提议者之前新增「公司研究」一步，显示原始 facts/risks/blocked/summary；引用跳转沿用同一 Recorder 的原 call 编号，不重新编号。批处理研究段不另建档案。

## 注意

研究员说明证据，不证明投资结论。手动研究只产[档案](agent.research_dossier.md)、不自动开链；所有判断仍为 Gate HOLD。
