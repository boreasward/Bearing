---
id: family.fundamentals
title: 公司基本面
group: family
version: 1
applies_to: [fetch-1, agent-4]
summary: "从 SEC companyfacts 保存财务披露观察；研究员只读截止前已入库事实，手工指引不是这个族，也不是新的外部数据源。"
related: [family.sec_text, family.earnings, agent.company_researcher, system.cutoff, system.adapter_registry]
sources: [investment_assistant/data/adapters/fundamentals.py, investment_assistant/data/sources/fundamentals.py, investment_assistant/data/research_rows.py, investment_assistant/data/repo.py]
---

## 是什么

把公司已披露的财务数字变成可追溯的观察记录，供[公司研究员](agent.company_researcher.md)使用。事实保留期间、单位、披露来源，不由模型补齐缺项。

## 怎么算 / 从哪来

- 主源 SEC companyfacts，无备源；沿用既有规范化，写 `us_research_observations` 的 `dataset='financials'`，按经济版本与内容去重。
- daily 范围为池、真实持仓、活跃链及活跃研究请求；研究/手动补采只处理显式标的，不扩成全池。
- 最新已知 `disclosed_at` 早于 session 前120天标滞后；这是覆盖提示，不代表本季应有却没有的数字。
- 研究工具按 `first_seen_at ≤ cutoff_at` 读取，整理最近8个财季的营收、毛利率、营业利润、摊薄 EPS、自由现金流与同比，有则给。手工 `guidance` 仍单独记录，不新增 guidance 数据族。

## 在系统里哪里看到

「数据 → 公司基本面」浏览财季、指标和值；研究档案与推理里的事实引用指回当次工具结果。

## 注意

披露日与系统首次见到的时间不同；今天取到旧财季数据也不能用于昨天的证据。缺值写缺口，不补零、不推断公司不存在该业务；Gate HOLD 不变。
