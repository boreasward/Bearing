---
id: system.agent_connection
title: "Agent 连接"
group: system
version: 1
applies_to: [siwc-1]
summary: "Agent 用你自己的 ChatGPT 订阅：在设置里登录一次，之后每次运行自动续期；额度用尽或连接失效时 Agent 整个跳过，规则计划与推送照常，数据只存在本机。"
related: [system.daily_workflow, agent.reject_reasons, system.stage_status, system.push]
sources: [investment_assistant/core/llm/chatgpt.py, investment_assistant/core/llm/connection.py]
---

## 是什么
Agent 的模型调用不再用 API 密钥，而是用你自己的 ChatGPT 订阅（Sign in with ChatGPT）。在「设置 → Agent 连接」点「连接 ChatGPT」，在本机浏览器里登录一次；之后服务每次运行 Agent 前自动换取新的访问凭据，你不用再管。协议版本 `siwc-1`。

## 怎么算 / 从哪来
- **登录**：点按钮后服务在本机 `127.0.0.1` 起一个只收一次回调的小监听，返回授权地址，你在浏览器里完成登录；10 分钟内没完成、点「取消」或服务重启，这次登录作废，状态回到原样。
- **自动续期**：登录得到的长期凭据每次续期都会顺延 30 天，所以每天运行一次就不会过期；访问凭据快过期时在下一个 Agent 单元之前自动刷新。凭据只存在本机数据库里（与 `.env` 里的密钥同一安全级别），不写日志、不经任何接口回显；设置页只显示状态、脱敏邮箱和到期时间。
- **选模型**：可选模型来自 ChatGPT 返回的清单（不写死任何模型名），在设置页下拉框里选，保存后下一次运行生效；所选模型之后不可用，Agent 记 `model_unavailable`，需要重新选。
- **额度用尽**：订阅的共享用量用完，当次运行其余单元记 `quota_exhausted` 跳过；到了下个周期自然恢复，不用重新登录。
- **连接失效**：续期被拒绝、账号变了，或 ChatGPT 明确拒绝这个账号的凭据时，状态变成"需要重新登录"。这次执行的 `agent` 节点整个记 `skipped`，原因 `agent_unavailable: not_connected`（页面写"ChatGPT 未连接"）；`review` 在没有待复查的链时同样整个跳过，有待复查的链时系统自己的复查（失效作废、无事维持）照常，只记一条"Agent 不可用"降级。运行中途才发现连接不可用时，发现它的那个单元记 `error`，本节点其余单元记 `skipped`、原因相同，不再逐个请求模型。规则计划、推送和复盘卡照常，推送里的 Agent 行写"Agent 未给出计划（ChatGPT 未连接）"。同一次失效只通知一次：每日工作流在这次执行收尾时发一条「ChatGPT 连接已失效」，之后的运行不再重复，重新连接后再失效才会再通知。
- **没有开启计划用量共享**：登录成功但这个账号没有开启共享时，状态仍是"已连接"，设置页提示"已登录，但这个账号没有开启计划用量共享"，Agent 记 `not_configured`，不发失效通知。
- **断开**：设置页的"断开"会请 ChatGPT 撤销长期凭据并清掉本机凭据；远端没有确认撤销时页面提示去 ChatGPT 设置里断开这个应用。换 ChatGPT 账号 = 先断开、再连接。

## 在系统里哪里看到
「设置 → Agent 连接」（状态、剩余有效期、上次续期、模型、连接与断开）；运行详情里 `agent` 节点的跳过原因；命令行 `python -m investment_assistant.core.llm.connection status`（不含凭据）。

## 注意
- 数据只在本机：模型请求直接从你的电脑发往 ChatGPT，凭据不经过任何第三方服务；请求不开启 ChatGPT 端的对话存储。
- 连接失效不会让每日工作流失败：Agent 是辅助轨道，规则计划不依赖它（见[每日工作流](system.daily_workflow.md)）。
- Agent 的价位仍来自模型输出，证据状态仍是"未验证"，不构成投资建议。
