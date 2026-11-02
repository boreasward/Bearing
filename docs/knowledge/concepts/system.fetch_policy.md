---
id: system.fetch_policy
title: "外部请求策略"
group: system
version: 3
applies_to: [fetch-1]
summary: "沿用 fetch-1 超时和主源重试；允许的最终失败才单次尝试已启用备源，真实次数合并，不叠加内部重试，落库失败不算成功。"
related: [system.backup_source, system.adapter_registry, family.bars, family.news, family.sec_filings, family.earnings, family.sec_text, family.fred, system.stage_status, system.push]
sources: [investment_assistant/data/fetch_policy.py, investment_assistant/data/http.py, investment_assistant/data/registry.py, investment_assistant/data/tally.py]
---

## 是什么
系统向外部取数（日线、资讯、财报日、SEC 申报、FRED、Discord）时，所有请求走同一套策略 `fetch-1`：别让一个卡住的请求拖垮整次运行，也别因为一次偶发失败就放弃一个目标。每个请求只有一个"目标名"（股票代码、序列号、`sec:…`），失败只影响这个目标，其余目标照常。

## 怎么算 / 从哪来
- **超时**：连接10秒，整个请求（含读完正文）30秒；Yahoo 另有35秒线程截止。主源默认至多3次与2秒/8秒等待；启用备源的目标还可能多一次备源请求，不能把旧单源时限当完整主备总时限。
- **重试**：只重试可能自己恢复的失败，最多 2 次，分别等 2 秒和 8 秒。可重试的类别是 `timeout`（超时）、`network`（连接、TLS、代理出错）、`http_5xx`（服务端 `500`、`502`、`503`、`504`）和 `http_429`（被限流，按对方给的等待时间，最多等 8 秒）。
- **不重试**：`http_4xx`（请求本身有问题，如 `404`、`403`）、`empty`（对方回了空结果）、`parse`（返回内容读不懂）和 `other`（其它）。重试也没用，立刻记失败。
- **Yahoo 的「空」怎么算**：yfinance 默认把连接、代理、TLS 失败吞成一张空表，系统分不出「网络抖动」和「真没数据」，所以建立共享会话时让它把异常抛出来：传输失败按 `network` / `timeout` 重试，只有 Yahoo 明确回「无价格数据 / 无时区」才是 `empty`。
- **备源不是重试**：主源最终 timeout/network/http_5xx/http_429/empty 且开关开、有第二源，才对同目标单次切换；4xx/parse/other 不切。财报日已启用 Nasdaq；日线正式仅 yfinance，Stooq 只有解析/补缺机制，未启用。见[主备规则](system.backup_source.md)。
- **不叠重试**：SEC/FRED/earnings 已有内部 policy 的入口外层单次，attempts 合并真实内部与备源次数。SEC 正文另用单次策略与跨 scope 日预算，超时同 session 不再下载。
- **推送不重发**：Discord 推送不是幂等的，读超时或 `5xx` 之后重发会出现重复消息，所以推送只在请求根本没发出去（连接失败）和被限流（`429`）时才重试。
- **目标级明细**：每次取数记到 `data_fetch_runs.detail.targets`，每个目标一项：`target`、`ok`、`attempts`（试了几次）、`error_kind`（上面的类别）、`http_status`、`reason`（脱敏后的原因）、`rows`（新写入或发现的条数）、`latest`（最新时点）、`elapsed_ms`（含重试等待的总耗时）。按目标名排序，一族至多 600 项。
- 错误原因里的密钥（如 FRED 的 key）一律替换成 `***`，并截断到 300 字。
- 新明细增加 `source_used`（源 id）、`fallback={tried, reason}`；每族每 scope 记行。主备都失败保留主源原因；取数成功但存储/事务失败仍是 ok=false、rows=0，不因此再试备源。

## 在系统里哪里看到
「数据」菜单各族的拉取详情；运行详情页的"拉取详情"表。旧记录没有 `targets`，只能看到失败项和计数。

## 注意
- 重试只是在同一次运行里多试几次；一个目标试完仍失败，这个数据族就是"部分成功"或"失败"（见[阶段状态](system.stage_status.md)），不会用旧数据冒充。
- 某个目标失败不会让整个阶段失败：数据族状态的判定没有变。
