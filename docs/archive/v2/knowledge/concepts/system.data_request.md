---
id: system.data_request
title: Agent 补查请求
group: system
version: 1
applies_to: [agent-4, daily-3]
summary: "request_data 只登记本轮不可见的补查；宿主在预算内采集后开启新截止时间的一轮，绝不让模型自行移动当前证据截止时间。"
related: [system.adapter_registry, system.cutoff, agent.company_researcher, agent.research_request, agent.version_chain]
sources: [investment_assistant/data/registry.py, investment_assistant/analysis/repo.py, investment_assistant/analysis/research_requests.py, docs/specs/2026-11-01-v2.12-v2.15-five-layers-and-agent-roles-design.md]
---

## 是什么

`request_data(family, ticker)` 让 Agent 指出证据缺口；不是在线查询，也不是立即拿到新数据。只有注册表中有 tool 的七个 per-ticker 族可选，标的仍受本阶段白名单限制。

## 怎么算 / 从哪来

- 工具先在 Recorder 缓存请求，运行结束与 `agent_runs` 同事务写 `data_requests`，返回 `queued` 与「本轮不可见；宿主补采后开新一轮」。它不在只读工具事务里插库。
- `agent` 节点先完成单元，再按 FIFO 处理研究请求，最后处理补查；`review` 尾部也调用同一个补查 handler，共用 agent + review 的1500秒剩余预算。剩余不足120秒则记 `deferred`。
- 按 `(family,ticker)` 去重并持族锁，走真实适配器；终态为 `fetched`、`failed`、`deferred`，只从 `queued` 结算一次。`fetched` 不必然有新行，只有 `rows>0` 才触发重跑。
- 每单元每天至多重跑一次，冻结新 cutoff。已开链走 `recheck`，在 `plan_reviews.detail.via` 记 `data_request`；未开链再尝试首版，仍受三次预算约束。`rerun_run_id` 仅补到已 fetched、有新行且尚无该字段的请求，不重复结算终态。
- 修订与补查重跑沿用已有研究员段，不再运行研究员。手动研究入口在自己的预算内同步处理补查、至多再出一版档案，仍不开链。

## 在系统里哪里看到

运行详情 `data_requests` 给出 `{queued,fetched,failed,deferred,reruns}` 五项计数；研究请求计数另用 `research_requests` 数字，不能混成同一字段形状。

## 注意

延期、零新增、重跑失败都不是证据已经补齐。原运行和旧截止时间照样可追溯；有新数据仍只说明输入改善，Gate HOLD 不变。
