# P4 复盘扩展 — 设计

日期：2026-10-25。基线 `main@0762b2d`。本文是设计，不是实施计划；实施计划另写。
上位文档：[roadmap v2](../archive/v2/roadmap/roadmap.md#81-v2-路线2026-10-23) 的 P4（§4.3 投递、§4.4 复盘、§4.6「现有 Codex 解读」一行）。在 [P1](2026-10-23-p1-rule-plans-and-push-design.md)、[P2](2026-10-23-p2-pool-and-market-context-design.md)、[P3](2026-10-24-p3-agent-track-design.md) 设计之上扩展，三者未改动的部分继续有效。

## 1. 目标与非目标

P4 补齐复盘这一环：

1. **三方轨道对比**：对同一批买入单元，并列规则计划、Agent 计划和你实际执行的结果（以 R 计），每一行都能追溯到计划、Agent 运行、决定和成交。
2. **月度复盘补全**：流程复查标记附上 3 个月复合收益明细；敏感性表改为按平仓时间排序；月度快照加入三方对比。
3. **周度与月度 Discord 摘要**：新增定时任务，周度自动冻结快照并推送；月度在有月末资金快照时生成并推送，缺快照时提醒一次。
4. **Codex 解读并入 Agent 轨道**：所有主列表单元的解读都由 Agent 轨道产出，Codex 客户端退役。

P1 已实现月度收益与分档、SPY 对照、滚动 3 个月复查标记和近似敏感性表（`monthly-1`）。P4 在这些基础上补齐，不推倒重写。

已确认的前提（2026-10-25）：

| # | 决策 | 结论 |
|---|---|---|
| 1 | Codex 合并方式 | Agent 统一解读，Codex 退役。无冲突的买入单元，解读直接取 Agent 建议的理由和反方，不再额外调用模型；卖出、观察和有冲突的买入单元，由 Agent 跑一个只读「解读」步骤，用同一套工具层和引用校验。删除 Codex 客户端和 `WORKBENCH_LLM_PROVIDER`，旧解读照常可查 |
| 2 | 周度推送 | 自动生成并推送。每周最后一个交易日收盘后的首次定时运行（通常是周六北京时间 08:00）推送该周快照。已有收盘后生成的快照就用最新那份，否则自动生成一份 |
| 3 | 月度推送 | 每天检查一次。上个月有月末资金快照、且还没推送成功时，生成并推送；到新月第一个周六仍缺快照，推一条提醒，每月只推一次。页面也可以手动推送 |
| 4 | 「实际执行」口径 | 三列用同一批单元。忽略、观察、到期未成交，实际记 0R 并单列计数；未平仓的部分按最新收盘价计浮动 R，并标「未平仓」 |

本文另定的默认值（审阅时可改）：

- 新定时任务 `workbench_review`：北京时间每天 08:00，迁移时插入为**关闭**，验收时再开启。推送频道默认 `signals`。
- 规则或 Agent 计划状态为 `invalid`（作废）时，该轨道不会下单，记 0R（`not_executable`）。
- 三方对比和周度快照的统计窗口，都按单元的信号日落在哪一周、哪一月来划分。

非目标：

- 不改规则、`plan-1`、`agent-1` 的建议流程与提示词，不改仓位参数。
- 不设晋升标准，不按对比结果替换主计划。
- 不在 Discord 里确认决定。
- 不做全组合净值回测；敏感性表仍是近似算法。
- 不改写或回填旧表数据，旧解读与旧快照保持原样。

Phase 0 正式 Gate 仍为 HOLD，所有摘要标「证据：未验证」，并注明样本小、不代表规则或 Agent 有效。

## 2. 设计原则

沿用 P1–P3 原则：只追加；按时点可还原；版本照记；失败不冒充空；凭据不进报告。P4 特别强调：

1. **同一批单元、同一种单位。** 三条轨道都对同一批单元算 R：规则和 Agent 用各自计划的止损定义 1R，实际执行用你所选计划的止损定义 1R。「没算出来」（待定、缺失）和「0R」（没交易）分开计数，均值只用已确定的值。
2. **推送只读冻结的快照。** 周度和月度摘要只从已冻结的快照渲染，不现场重算；要更正，就生成一份新快照指向旧快照，再推送一次。
3. **推送幂等。** 每次推送尝试都写一行记录；同一周期推送成功后，定时任务不再重复推送。
4. **解读不改动作。** 解读只描述证据，任何价位或动作字段在代码层面一律丢弃（沿用 P1 §8 与 P3 §2.4）。

## 3. 数据模型

迁移 `020_review_expansion.sql`。对旧表只允许以下改动：加列、放宽或替换 CHECK 约束、插入一行定时任务。不 UPDATE、不 DELETE、不回填。

### 3.1 `agent_runs` 增加 `mode`

- 新列 `mode TEXT NOT NULL DEFAULT 'propose' CHECK (mode IN ('propose','interpret'))`。已有行都按默认值记为 `propose`。
- 原约束 `CHECK ((status = 'ok') = (plan_id IS NOT NULL))` 替换为 `CHECK ((mode = 'propose' AND status = 'ok') = (plan_id IS NOT NULL))`。这条约束是匿名的，迁移用 `DO` 块，按 `pg_get_constraintdef` 包含 `plan_id IS NOT NULL` 找到它再删除。
- 新增 `CHECK (mode = 'propose' OR status <> 'pass')`：解读运行没有 `pass` 状态。
- 解读运行的工具调用仍写入 `agent_tool_calls`，`phase` 记为 `propose`（单阶段运行；表的 CHECK 不改）。
- 新索引 `(ticker, signal_date, mode, created_at DESC)`。

### 3.2 `signal_briefs` 增加 `agent_run_id`

新列 `agent_run_id UUID REFERENCES agent_runs(id)`，可空。P4 之后写入的解读都指向产生它的 Agent 运行；P4 之前 Codex 写的解读保持为空。

### 3.3 `review_pushes` — 每次复盘推送尝试一行

| 字段 | 说明 |
|---|---|
| `id` | BIGSERIAL |
| `kind` | `weekly` / `monthly` / `monthly_reminder` |
| `period` | 周一（周度）或月初 1 日（月度与提醒） |
| `review_id` | 推送的快照 id（`weekly_reviews` 或 `monthly_reviews`）；提醒为空 |
| `trigger_source` | `scheduled` / `manual` |
| `sent` | 是否全部送达 |
| `messages` | 已发出的消息条数 |
| `reason` | 未送达的原因（`task_disabled`、`send_failed: …` 等），已脱敏 |
| `created_at` | `clock_timestamp()` |

约束：`CHECK (sent OR reason IS NOT NULL)`；索引 `(kind, period, created_at DESC)`。

### 3.4 定时任务

`INSERT INTO scheduled_jobs (name, time_local, weekday_mask, timezone, enabled) VALUES ('workbench_review', '08:00', '1-7', 'Asia/Shanghai', FALSE) ON CONFLICT (name) DO NOTHING`。

## 4. 三方轨道对比（`tracks-1`）

### 4.1 单元集合

如果一个单元有 `rule` 计划，或有 `mode = 'propose'` 的 Agent 运行，并且信号日落在统计窗口内，它就进入对比集合。这与 P3 中「每个无冲突买入单元都有规则计划或 Agent 运行」的覆盖口径一致：规则因 ATR 跳过的单元，靠 Agent 运行进入集合。

单元的分池沿用 `review.pool_of`：优先取规则计划冻结的 context 中的分层，其次取信号日当时记录的分层；Agent 计划同理。

### 4.2 每条轨道的结果

每条轨道给出 `{status, r, ...}`。只有 `r` 不为空的结果才计入均值和中位数。

**规则**（该单元的 `rule` 计划，模拟用 `plan_shadow.simulate`）：

| status | 条件 | r |
|---|---|---|
| `missing` | 没有规则计划 | 空 |
| `not_executable` | 计划状态为 `invalid` | 0 |
| `pending` | 模拟结果为 `pending` | 空 |
| `expired` | 有效期内没成交 | 0 |
| `open` | 已成交、未全部平仓 | 模拟给出的浮动 R |
| `closed` | 已全部平仓 | 模拟给出的 R |

**Agent**：取该单元最新一次 `propose` 运行。

- 运行为 `pass`：status 记 `pass`，r 为 0。
- 运行为 `ok`：对它的 `agent` 计划套用上表的规则（`not_executable` 到 `closed`）。
- 运行为 `rejected`、`error`、`skipped`，或没有运行：status 记 `missing`，r 为空，同时记下运行状态与原因。

**实际**：取该单元当前的决定（决定链的最新一条）。

| status | 条件 | r |
|---|---|---|
| `not_executed` | 最终动作是 `ignore`、`watch` 或 `sell` | 0 |
| `not_executed` | 最终动作是 `buy`，所选计划已过有效期，但没有关联的买入成交 | 0 |
| `not_executed` | 没有决定，且该单元所有计划的有效期都已过 | 0 |
| `pending` | 没有决定或没有成交，但仍有计划在有效期内 | 空 |
| `no_plan` | 有成交，但决定没有关联计划 | 空 |
| `open` | 有关联成交，仍有持仓 | 已平仓部分与浮动部分按股数加权的 R |
| `closed` | 关联成交已全部平仓 | 已平仓 R |

R 的算法：

- 风险价取所选计划的止损，换算到买入日的拆股口径（同 `discipline._at_basis`）。
- 已平仓部分沿用纪律指标的算法，即 FIFO 匹配后，按股数加权的 (卖出价 − 每股成本) ÷ (每股成本 − 止损)。
- 未平仓部分：每股成本取买入价加上分摊到每股的手续费，和止损一起换算到当前拆股口径，用最新收盘价计浮动 R。
- 实际一列另外记下 `plan_kind`（`rule` / `agent` / `own`），即你照哪条轨道执行。

### 4.3 汇总

对 `all`、`core`、`extended` 三组，每条轨道给出：`units`（单元数）、`determined`（r 不为空的个数）、`pending`、`missing`、`mean`、`median`、`sum`、`wins`（r > 0 的个数）、`by_status`（各状态计数）。实际一列另给 `by_plan_kind`。只给计数和描述统计，不做显著性检验。

### 4.4 每行可追溯

每个单元一行，包含：`ticker`、`signal_date`、`pool`；`rule`：`plan_id` 与结果；`agent`：`run_id`、`run_status`、`run_reason`、`plan_id` 与结果；`actual`：`decision_id`、`final_action`、`plan_id`、`plan_kind`、`buy_trade_ids` 与结果。

### 4.5 用在哪里

| 位置 | 版本 | 内容 |
|---|---|---|
| 周度快照 | `review-3` → `review-4` | 新键 `tracks`（单元行 + 汇总），窗口为该周的信号日 |
| 月度快照 | `monthly-1` → `monthly-2` | 新键 `tracks`，窗口为该月的信号日 |
| 实时接口 | `tracks-1` | `GET /api/workbench/review/tracks?since=&until=`，默认最近 90 天，不冻结 |

已生成的快照保持不变；页面按快照里有无 `tracks` 键决定是否显示对比块。

## 5. 月度复盘补全（`monthly-2`）

| 项 | 变化 |
|---|---|
| 流程复查 | 保留字符串 `review_flag`。新增 `review`：`{flag, months: [{month, return, spy_return}], compound, spy_compound}`，三个月中任一个月缺数据时，`compound` 与 `spy_compound` 为空 |
| 敏感性表 | 当月已平仓 R 先按平仓时间（`closed_at`）排序，再算累计回撤。`monthly-1` 是按标的顺序拼接的，回撤可能被低估。新增 `count`（交易笔数），仍注明为近似 |
| 三方对比 | 新键 `tracks`（§4） |

## 6. 周度与月度推送

### 6.1 定时任务 `workbench_review`

`tasks/workbench_review.py` 注册进调度器。每次运行依次处理周度和月度，结果写入 `job_reports`。手动运行（`POST /api/jobs/workbench_review/run`）的逻辑与定时运行相同，只是 `trigger_source = manual`。已经推送成功的周期，两种运行都不会重复推送。

**到期周**：设 `s` 为最近一个已收盘的交易日（`bars.latest_closed_session(now)`）。如果 `s` 的下一个交易日落在下一周，`s` 所在的周到期；否则上一周到期。月度同理：`s` 的下一个交易日落在下个月时，`s` 所在月到期，否则上个月到期。休市日由交易日历处理。

**周度**：

1. 到期周 `W` 已有推送成功的 `weekly` 记录 → 跳过。
2. 如果 `W` 的快照链最新一份生成于 `W` 最后一个交易日收盘之后（即 `latest_closed_session(created_at) >= W 的最后交易日`），沿用它；否则生成一份新快照，备注「自动生成」，并指向原来那份。
3. 推送这份快照，写一行 `review_pushes`。

**月度**：

1. 到期月 `M` 已有推送成功的 `monthly` 记录 → 跳过。
2. `M` 内有可用资金快照（即 `monthly` 选月末快照的同一规则）：
   - 如果最新月度快照生成于 `M` 最后一个交易日收盘之后，并且晚于最新一条资金快照和出入金的录入时间，沿用它；
   - 否则生成新快照，备注「自动生成」，指向原来那份。
   - 推送这份快照，写一行 `review_pushes`。
3. `M` 内没有资金快照：如果北京日期已到 `M` 次月的第一个周六，且还没推送成功过 `monthly_reminder`，推一条提醒，写一行 `review_pushes`；否则不推。

快照生成失败（例如别人刚更正过、出现链冲突）记入运行报告的 `errors`，本次不推送，下次运行重试。

### 6.2 推送内容

用 `notifier.send_notifications('workbench_review', ...)` 发送，沿用开关、频道和错误处理逻辑。默认 `task_channels['workbench_review'] = 'signals'`、`task_enabled['workbench_review'] = True`（任务本身默认关闭，见 §3.4）。每条消息 ≤ 10 个 embed、≤ 6000 字符，每个字段 ≤ 1024 字符，超长截断（沿用 `_clip_field` / `_fit_message`）。

**周度**（一条消息），标题 `📅 周度复盘 · {start} – {end}`：

| 字段 | 内容 |
|---|---|
| 概览 | `已决定 {n} · 未处理 {n} · 计划外交易 {n} · 已平仓 {n}` |
| 轨道对比 | 每条轨道一行：`{规则/Agent/实际} {units} 单元 · 已定 {determined} · 均值 {mean:+.2f}R · 中位 {median:+.2f}R · 合计 {sum:+.2f}R`；均值为空时写「均值 —」。最后一行：`实际：未执行 {n}（记 0R）· 未平仓 {n} · 照规则 {n} · 照 Agent {n} · 自定 {n}` |
| 规则 | 每个分池、每条买入规则一行：`{核心/扩展} {rule_id} 触发 {triggers} · 计划 {plans} · 成交 {filled} · 均值 {r_mean}R` |
| 纪律 | `追高 {n} · 过早止盈 {n} · 死扛亏损 {n} · 计划外 {n} · 合规率 {x%}`；合规率为空时写「—」 |
| 说明 | `描述性统计，样本小且重叠，不代表规则或 Agent 有效。证据：未验证` 和链接 `{public_url}/#review/weekly` |

**月度**（一条消息），标题 `🗓 月度复盘 · {YYYY-MM}`：

| 字段 | 内容 |
|---|---|
| 收益 | `本月 {return:+.1%}（{分档}）· SPY {x:+.1%} · 超额 {y:+.1%}`；缺快照时写「缺资金快照，无法计算」 |
| 流程复查 | `正常` / `⚠️ 需复查：3 个月复合 {c:+.1%}，SPY {s:+.1%}` / `样本不足` |
| 敏感性（近似） | 每个风险比例一行：`{pct} 收益 {return:+.1%} · 回撤 {dd:.1%}`；末尾注明交易笔数 |
| 轨道对比 | 同周度 |
| 纪律 | 同周度 |
| 说明 | 同周度，链接改为 `#review/monthly` |

**提醒**，标题 `⏰ 月度复盘待录入 · {YYYY-MM}`，正文：`缺 {YYYY-MM} 月末资金快照。录入后在「复盘 → 月度复盘」生成并推送。` 附链接 `#review/account`。

### 6.3 手动推送

`POST /api/workbench/review-pushes`，请求体 `{kind: 'weekly'|'monthly', period}`：推送该周期快照链的最新一份，写一行 `review_pushes`（`trigger_source = manual`）。

- 不检查是否已推送过，即强制推送；
- 该周期没有快照返回 404；
- `kind` 不合法、`period` 不是周一或 1 日返回 400。

`GET /api/workbench/review-pushes?limit=` 列出最近的推送记录。

## 7. Codex 解读并入 Agent 轨道

### 7.1 由谁产出解读

范围是本交易日、尚未决定、属于主列表（核心池与持仓）的单元。扩展池不产出解读，与以前一致。

| 单元 | 解读来源 | `signal_briefs` 行 |
|---|---|---|
| 无冲突买入 | 该单元的 `propose` 运行 | `ok`/`pass`：`status = ok`。输出为 `summary` = 建议摘要，`support` = `thesis`，`contra` = 反方 `risks`（反方失败时为空），`conflicts` = `[]`；`prompt_version = 'agent-1'`。`rejected`/`error`/`skipped`：`status = fallback`，输出为规则版摘要，`error` 填原因的中文 |
| 卖出、观察、有冲突的买入 | `interpret` 运行（§7.2） | `ok`：`status = ok`，`prompt_version = 'agent-brief-1'`。`rejected`：`status = error`，`error` 填原因中文，内容不展示。`error`/`skipped`：`status = fallback` |

- 解读行与产生它的运行在同一个事务中写入；`ok` 建议的事务内依次写计划、运行、工具调用、解读。
- `model` 填运行的模型名；`input_refs` 填该运行全部工具调用的 `call:<seq>`，按 seq 排序；`cutoff_at` 与运行一致；`agent_run_id` 指向该运行。
- 规则版摘要沿用现有 `_fallback_summary` 的写法：「规则候选：…；建议动作 …。LLM 未参与。」。

### 7.2 解读运行（`agent-brief-1`）

- 单阶段图：`interpret ⇄ 工具 → check → END`，工具与上下文同 P3 §5，每阶段最多 8 轮工具调用，超出则 `rejected / tool_rounds_exceeded`。
- 运行行：`mode = 'interpret'`，`agent_version = 'agent-brief-1'`，`pool = 'main'`。状态取值：`ok`、`rejected`、`error`、`skipped`；原因取值同 P3 §6.4。
- 校验 `check_interpretation`，按顺序检查：
  1. 去掉一层围栏后能解析成 JSON 对象，否则 `malformed`；
  2. `summary` 是字符串，`support`、`contra`、`conflicts` 是列表（可为空），每一项有字符串 `text` 和非空 `refs`，否则 `malformed`；
  3. 所有 `refs` 都在本次运行真实返回过的引用之内，否则 `invalid_citation`。
  - 输出中其他键（`action`、价位等）丢弃。
- 提示词：

```
你是美股波段研究助手，为一个决策单元写解读，只解读、不给买卖动作。
只能通过工具获取数据；工具只返回截止时间之前的数据，你不能指定日期。先调用 get_trigger 查看触发了哪些规则。
每个工具结果带有 ref（如 call:3）。每条结论都必须引用支撑它的 ref，只能引用本次真实返回过的 ref；引用不存在的 ref 会使整条解读作废。
资讯只有标题与链接，未核验原文。
完成后只输出一个 JSON 对象，不要输出其他文字：
{"summary": 一句话, "support": [{"text": 字符串, "refs": [...]}], "contra": [...], "conflicts": [...]}
support 写支持规则方向的证据，contra 写反证，conflicts 写互相矛盾或信息不足之处。不要输出动作、价位或任何交易指令。
```

### 7.3 批处理

- 规则循环里不再生成解读，只有 Agent 阶段生成。
- Agent 阶段的顺序：
  1. 主列表无冲突买入单元的 `propose`；
  2. 主列表其他单元的 `interpret`；
  3. 扩展池无冲突买入单元的 `propose`。
  - 三组共用 1500 秒时限、额度用尽和每日 token 上限这三条规则。
- 跳过与重试按 `mode` 分开计数：
  - 已有 `ok`（`propose` 还包括 `pass`）的运行就跳过；
  - 同一 `mode` 已有 3 次运行，则写一行 `skipped / max_attempts`，只写一次。
- 每个被跳过的单元，也写一行运行和一行规则版解读（扩展池只写运行）。
- 所有「按单元查 Agent 运行」的查询都按 `mode` 过滤：今日视图、覆盖率、手动运行的冲突判断、三方对比、批处理的跳过判断都只看 `propose`；解读只看 `interpret`。`agent_tokens_on` 不过滤，两种运行合计计入 token 上限。
- 运行报告：`agent.runs` 只记 `propose`；新增 `agent.briefs: {ticker: 'ok' / '<status>: <reason>'}`；`briefs` 键保留，值为解读行的状态；`tokens` 两种运行合计。

### 7.4 退役

- 删除 `workbench/codex_client.py` 及其测试。
- 删除 `briefs.provider_client`、`generate`、`SYSTEM_PROMPT`、`build_payload`、`_validate` 和 DeepSeek / 关闭两个客户端。
- 删除 `Deps.brief_client`，以及环境变量 `WORKBENCH_LLM_PROVIDER`、`WORKBENCH_CODEX_BIN`、`WORKBENCH_CODEX_MODEL`。
- `hermes/deepseek_client.py` 其他功能仍在用，不动。
- 旧 `signal_briefs` 行（`prompt_version = 'workbench-brief-1'`）照常在「今日」与标的页显示；决定的 `brief_id` 仍可指向任意属于该单元的解读。

### 7.5 展示

- **今日**：无冲突买入单元的解读如果来自 `agent-1`，内容已经在「Agent 建议」块里展示，解读块只写一行「解读见 Agent 建议」；其他解读照旧显示，模型名改为显示 `brief.model`。
- **推送**：`prompt_version = 'agent-1'` 的解读不再附在计划行后（Agent 行已经包含摘要和第一条反方），其他解读照旧。

## 8. 接口与页面

### 8.1 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/workbench/review/tracks?since=&until=` | §4 实时对比 |
| GET | `/api/workbench/review-pushes?limit=` | 推送记录 |
| POST | `/api/workbench/review-pushes` | §6.3 手动推送 |
| GET | `/api/workbench/agent-runs?since=` | 扩展：每行带 `mode`；覆盖率只算 `propose` |
| POST | `/api/jobs/workbench_review/run` | 已有的通用入口，§6.1 |

### 8.2 页面

- **复盘 → 轨道对比**（新标签 `tracks`）：
  - 汇总表：三列轨道 × 全部/核心/扩展；
  - 单元表：每行显示三条轨道的状态与 R；点击计划 id 跳到标的页；点击 Agent 运行可展开工具调用（复用今日页的引用展开）。
- **复盘 → 周度 / 月度**：快照卡片显示轨道对比块（有 `tracks` 时）、月度复查明细；每张卡片有「推送到 Discord」按钮，按钮旁显示最近一次推送的结果。
- **设置 → 定时任务**：`workbench_review` 自动出现在列表里，不需要额外开发。

## 9. 测试与交付

- **纯函数**：
  - 三方对比：各轨道的每个状态、R 与浮动 R、拆股换算、按状态与轨道计数、按池汇总；
  - 到期周与到期月：休市周五、月末落在周末；
  - 复查明细；敏感性排序；
  - 三种推送的文案与 Discord 限制；
  - `check_interpretation` 三步校验；
  - 由建议生成解读的转换。
- **图**：用脚本化的假模型跑通解读运行的 `ok`、`malformed`、`invalid_citation`、`tool_rounds_exceeded`。
- **数据库**：在一次性 PostgreSQL 上执行迁移 020，检查以下几点：
  - `mode` 默认值，以及旧行仍满足新约束；
  - `interpret`/`ok` 不带计划可以写入，`propose`/`ok` 不带计划会被拒绝；
  - `interpret`/`pass` 会被拒绝；
  - `signal_briefs.agent_run_id`、`review_pushes` 的约束；
  - 定时任务那一行插入为关闭。
- **批处理**：
  - 解读顺序与时限；
  - 按 `mode` 分开重试；
  - Agent 关闭时，每个主列表单元都有一行规则版解读；
  - 规则循环里不再生成解读；
  - 今日视图与覆盖率不受解读运行影响。
- **任务**：
  - 周度：自动生成、沿用收盘后生成的快照、收盘前生成的快照会被更替、推送成功后不再推、推送失败下次重试；
  - 月度：缺快照时只提醒一次、补录快照后生成并推送；
  - 手动推送强制发送。
- **场景**：端到端用例 `test_review_expansion_end_to_end`。从计划开始，经过规则与 Agent 影子跟踪、照 Agent 计划成交、另一个单元被忽略，到周度快照里三列与可追溯的 id，再到周度推送和月度推送。
- **前端**：vitest 覆盖轨道对比标签、快照卡片里的对比与复查明细、推送按钮、「解读见 Agent 建议」。
- **文档（DoD）**：更新 `docs/us-workbench.md`：新增「复盘推送」「轨道对比」两节，删掉 Codex 那一段，写明手动入口、验证入口和验证示例。

以下几项都需要单独确认：在真实库执行迁移 020（执行前先 `pg_dump` 备份）、开启 `workbench_review`、发真实推送。

## 10. 与现有能力的关系

| 现有能力 | 处理 |
|---|---|
| `review.weekly_stats` / `generate_weekly` | 加 `tracks`，`CALC_VERSION = 'review-4'` |
| `monthly.py` / `generate_monthly` | 加 `review_detail`，敏感性按平仓时间排序，加 `tracks`；`MONTHLY_VERSION = 'monthly-2'` |
| `plan_shadow.simulate`、`discipline._at_basis` | 复用，不改 |
| `briefs.py` | 改为解读行的构造（来自建议、来自解读运行、规则版）；不再调用模型 |
| `codex_client.py` | 删除 |
| `agent/graph.py` | 新增 `INTERPRET_PROMPT`、`run_interpret`；`run_unit` 不变 |
| `agent/citations.py` | 新增 `check_interpretation` |
| `service.run_batch` / `_agent_stage` | 规则循环不再生成解读；Agent 阶段三组 |
| `digest.py` / `templates.py` | 日推送跳过 `agent-1` 解读；新增周度、月度、提醒三种模板 |
| `notifier.send_notifications` | 复用，任务名 `workbench_review` |
| `scheduler.REGISTRY` | 新增 `workbench_review` |
