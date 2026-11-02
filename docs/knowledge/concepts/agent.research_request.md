---
id: agent.research_request
title: 指定标的研究请求
group: agent
version: 1
applies_to: [agent-4, daily-3]
summary: "不用等规则信号即可提交标的与问题，先补采再冻结证据；daily 忙时成功入队，研究只出档案，不自动创建计划或链。"
related: [agent.company_researcher, agent.research_dossier, system.data_request, system.feature_flags]
sources: [investment_assistant/analysis/repo.py, investment_assistant/analysis/agent/research_check.py, investment_assistant/analysis/research_requests.py, investment_assistant/api/routes/research.py, docs/specs/2026-11-01-v2.12-v2.15-five-layers-and-agent-roles-design.md]
---

## 是什么

研究页的第二入口接收标的、可选问题与期限。它与信号单元使用同一 `agent-4` 研究、提议、反方、校验能力，但输出只进入[研究档案](agent.research_dossier.md)：即使 `action=buy` 也不创建 `trade_plan` 或计划链，不自动触发已有链复查。

## 怎么算 / 从哪来

- 问题最多500字，每天手动研究最多5次；`request_id` 幂等回放。开关关闭返回 `feature_disabled`，超过配额返回 `research_quota`。
- daily 未运行时后台启动，独立900秒窗口；daily 已运行仍返回 HTTP 200 的 `{id,status:'queued'}`，由 agent 节点按 FIFO、剩余预算处理。不是 `daily_running` 409。token 同计每日总量；已启动研究与后来开始的 daily 共用族锁，不另加全局互斥。
- 宿主先对该标的补采 bars/news/sec_filings/earnings/fundamentals/sec_text/sector，再冻结 cutoff，问题放 `[fact:question]`。补查只在预算内同步处理，有新行至多再出一版，仍遵守本轮不可见。
- 请求只有五态：`queued` 排队中、`running` 研究中、`waiting_data` 等待数据、`concluded` 已得结论、`closed` 已结束；合法前态可多次转换，waiting_data 会被队列再次取到。结论版本另可为 `error` 出错，不能把 error 加进请求状态集合。
- 模型/额度/超时失败记 error 版本与原因，请求回队列；三次失败由业务层关闭为 `max_attempts`。也可手动结束，30个自然日无新版自动结束；队列等待本身不冒充一次成功研究。

## 在系统里哪里看到

「研究」列表查看状态与截止时间，创建后用返回 id 读回档案；请求数在运行详情 `research_requests` 中是数字。

## 注意

`concluded` 表示已有结论，不表示建议买入。数据不足可以等待，未知催化或下一步要显示未记录，不让模型补造；Gate HOLD 不变。
