# P3 Agent 轨道 — 设计

日期：2026-10-24。基线 `main@23d0fb6`。本文是设计，不是实施计划；实施计划另写。
上位文档：[roadmap v2](../archive/v2/roadmap/roadmap.md#81-v2-路线2026-10-23) 的 P3（§4.2 Agent 并列建议、§4.6 Agent 运行时、§4.7 进化）。在 [P1 设计](2026-10-23-p1-rule-plans-and-push-design.md) 与 [P2 设计](2026-10-23-p2-pool-and-market-context-design.md) 之上扩展，二者未改动的部分继续有效。

## 1. 目标与非目标

为每个买入单元增加一条与规则主计划并列的 **Agent 建议**：Agent 通过工具读取截止时间之前的数据，独立给出动作、入场区间、止损、T1 和带引用的理由；「反方」步骤找反证并核对引用；所有价位可追溯到写入数据库的工具调用记录。Agent 建议生成 `agent` 计划并影子跟踪，在「今日」和推送中与规则计划并列展示，你可以选择照它执行。

已确认的前提（2026-10-24）：

| # | 决策 | 结论 |
|---|---|---|
| 1 | 模型 | 智谱 `glm-5.3`，OpenAI 兼容接口；模型与接入地址配置化 |
| 2 | token 预算 | 默认不设每日上限（受智谱 5 小时窗口额度约束）；每次运行照记 token；可选配置上限。窗口额度用尽视为 `quota_exhausted`，当次批处理停 Agent 轨道，规则轨道照常 |
| 3 | 独立性 | Agent 看不到规则计划价位；看得到触发了哪些规则及其输入 |
| 4 | 扩展池 | 扩展池买入单元也跑 Agent，排在主列表（核心池与持仓）之后；扩展池的 Agent 建议不推送 |

本文另定的默认值（审阅时可改）：Agent 可以输出「不建议买入」（`pass`）；Agent 计划的有效期与移动止损沿用 `plan-1`（T+1 至 T+3，T1 后 3 ATR 移动止损），Agent 只定四个价位；每阶段最多 8 轮工具调用；价位必须在 T 日收盘价 ±50% 内；Agent 阶段在批处理开始 1500 秒后不再启动新单元；同一单元自动重试最多 3 次。

非目标：不改规则、计划公式（`plan-1`）与规则计划；不设晋升标准，Agent 不改主计划；不做三方轨道对比、周度与月度推送（P4）；不改 Codex 解读（P4 合并）；不做多 workflow 投票；不暴露 MCP；不接券商、不自动下单。Phase 0 正式 Gate 仍为 HOLD，Agent 建议同样标「证据：未验证」。

## 2. 设计原则

沿用工作台、P1、P2 原则：只追加；按时点可还原；版本照记；失败不冒充空。P3 特别强调：

1. **截止时间不归模型。** 交易日与截止时间由批处理注入工具层，工具不接受日期参数，模型无法读取截止时间之后写入的数据。
2. **价位必须可追溯。** 每次工具调用的参数、返回值与哈希写库；建议中的每个价位和每条理由都引用这些调用。引用不存在的调用，整条建议作废，不做部分挽救。
3. **两条轨道互不拖累。** Agent 阶段在规则计划全部写入之后才开始；Agent 的任何失败只记在 Agent 轨道，不影响规则计划与推送。
4. **反方只写风险。** 反方步骤的输出只进入风险提示，代码层面忽略其中任何价位或动作。

## 3. 数据模型

迁移 `019_agent_track.sql`。时间戳为 `TIMESTAMPTZ`。

### 3.1 `trade_plans` 增加 `agent`

`kind` 的 CHECK 从 `('rule','own')` 放宽为 `('rule','own','agent')`（P1 设计 §3.1 已预留）；新增部分唯一索引 `(ticker, signal_date, plan_version) WHERE kind = 'agent'`。这是对旧表唯一的改动：只放宽约束、加索引，不改写或回填任何行。

### 3.2 `agent_runs` — 每个单元每次 Agent 运行一行

| 字段 | 说明 |
|---|---|
| `id` | UUID，由程序生成（同时作 LangGraph `thread_id`） |
| `ticker`, `signal_date` | 所属决策单元 |
| `pool` | `main` / `extended` |
| `attempt` | 该单元第几次运行（从 1 起） |
| `agent_version` | `agent-1`（图结构、提示词、工具集合的版本） |
| `provider`, `model` | 如 `zhipu`、`glm-5.3` |
| `cutoff_at` | 工具层使用的截止时间 |
| `status` | `ok` / `pass` / `rejected` / `error` / `skipped` |
| `reason` | 非 `ok`/`pass` 时必填，取值见 §6.4 |
| `detail` | `error` 时脱敏后的异常文本（≤ 300 字符，密钥替换为 `***`），其余为空（复核修复补充） |
| `output` | 校验后的建议（JSONB），`ok`/`pass` 时有值 |
| `counter` | 反方结果（JSONB），可空 |
| `plan_id` | `ok` 时指向 `agent` 计划，其余为空 |
| `input_tokens`, `output_tokens` | 本次运行合计 |
| `started_at`, `finished_at`, `created_at` | |

约束：`(status = 'ok') = (plan_id IS NOT NULL)`；`status IN ('ok','pass') OR reason IS NOT NULL`。

### 3.3 `agent_tool_calls` — 工具调用记录

`id`、`run_id`（→ `agent_runs`）、`seq`（运行内从 1 递增，即引用编号 `call:<seq>`）、`phase`（`propose` / `counter`）、`tool`、`args`、`result`（JSONB）、`result_sha256`（规范化 JSON 的 SHA-256）、`called_at`；`(run_id, seq)` 唯一。

运行结束时，`agent` 计划、`agent_runs` 行与全部工具调用在同一事务写入（先计划、再运行、再调用）；运行中途崩溃则不留业务行，LangGraph 检查点保留中间状态。

### 3.4 LangGraph 检查点

用 `langgraph-checkpoint-postgres` 的 `PostgresSaver`，表（`checkpoints` 等）由其自带的 `setup()` 创建，不进本项目迁移；由手动命令 `python -m investment_assistant.workbench.agent setup` 执行，真实库执行前单独确认。检查点表不可用时 Agent 运行记 `error / checkpoint_unavailable`，规则轨道不受影响。

## 4. 运行时与模型

| 项 | 规则 |
|---|---|
| 依赖 | `langgraph`、`langgraph-checkpoint-postgres`、`langchain-openai`（写入 `requirements.txt`） |
| 模型 | `langchain_openai.ChatOpenAI`，`base_url` 指向智谱 OpenAI 兼容端点，`temperature = 0.2`，单次请求超时 90 秒，客户端重试 2 次 |
| 环境变量 | `WORKBENCH_AGENT_PROVIDER`（`zhipu` 默认 / `off`）；`ZHIPU_API_KEY`；`WORKBENCH_AGENT_MODEL`（默认 `glm-5.3`）；`WORKBENCH_AGENT_BASE_URL`（默认 `https://open.bigmodel.cn/api/coding/paas/v4`，即 GLM Coding Plan 端点，验收时核对）；`WORKBENCH_AGENT_DAILY_TOKENS`（可选，正整数，默认不设） |
| 未配置 | `provider = off` → `skipped / disabled`；未设 `ZHIPU_API_KEY` → `skipped / not_configured`；两种情况都不发请求 |
| 额度用尽 | HTTP 429 且响应体错误码属于额度类（初值 `1113`、`1308`、`1310`，验收时按真实响应核对）→ `error / quota_exhausted`，当次批处理其余单元 `skipped / quota_exhausted`；其他 429 与 5xx 由客户端重试，仍失败为 `error / model_error` |
| 密钥 | 只从环境变量读取；写入运行报告、`reason` 或日志的文本中出现密钥一律替换为 `***` |
| 每日上限 | 设置 `WORKBENCH_AGENT_DAILY_TOKENS` 时，当日（纽约日期，按 `started_at`）已用 token ≥ 上限后的单元 `skipped / token_budget` |

## 5. 工具层

普通 Python 函数，放在独立模块，不依赖 LangGraph；LangGraph 只把它们包成工具。批处理为每个单元构造工具上下文：`ticker`、`session`、`cutoff_at`、允许的标的、调用记录器。所有读取都经现有 `repository` 的按时点读取（`cutoff_at`），只返回 `price_date <= session` 的 K 线。

| 工具 | 参数（模型可填） | 返回 |
|---|---|---|
| `get_trigger` | 无 | 单元的触发规则：`rule_id`、动作、规则输入；**不含任何计划价位** |
| `get_price_bars` | `ticker`、`lookback`（默认 60，1–120） | 日线 `date, open, high, low, close, volume`，旧到新 |
| `get_indicators` | `ticker` | `last_close`、`atr14`（`plans.atr14`）、`ma20/ma50/ma200`（`close`）、`high_20/low_20`、`high_252/low_252`、`avg_volume_20`、`volume_ratio`（当日量 ÷ 20 日均量）、`rs20/rs60`（`adj_close` 收益减 SPY）；数据不足的项为 `null` |
| `get_filings` | `days`（默认 30，1–90） | SEC 文件元数据（截止时间前已受理） |
| `get_news` | `days`（默认 7，1–14） | 资讯标题、链接、来源、发布时间（未核验原文） |
| `get_earnings` | 无 | 下次财报日期与状态 |
| `get_market_context` | 无 | 该交易日的区制快照：标签、广度、行业强弱、利差 |
| `get_position` | 无 | 当前持仓股数与成本，或无持仓 |

- `ticker` 只允许单元标的、`SPY`、`SMH`、`XLK`、`QQQ`；其他标的、越界参数返回 `{"error": ...}`，同样记录。
- 每次调用（含报错）记录一行，返回给模型的内容为 `{"ref": "call:<seq>", "data": ...}` 或 `{"ref": ..., "error": ...}`。
- 价格类工具为 `get_price_bars`、`get_indicators`。

## 6. Agent 流程（`agent-1`）

### 6.1 图

```
START → propose ⇄ 工具 → check ──(作废)──► END
                           └─(通过)→ counter ⇄ 工具 → finish → END
```

- **propose**：系统提示 §6.5；首条用户消息只给标的、信号日、分层与「数据截至 T 日收盘」。模型自行调用工具，最后输出 JSON。
- **check**：纯代码校验（§6.2），不调用模型。
- **counter**（反方）：系统提示 §6.5；输入建议 JSON；可调用工具；输出风险列表。
- 每阶段最多 8 轮带工具调用的模型回复。propose 超限 → `rejected / tool_rounds_exceeded`；counter 超限 → 反方状态 `tool_rounds_exceeded`，建议照常保留。
- token 为全部模型回复 `usage_metadata` 之和。

### 6.2 校验（check）

按顺序，任一不满足即停：

1. 去掉首尾空白和一层 ```` ```json ```` 围栏后解析为 JSON 对象，否则 `malformed`。
2. `action` ∈ `buy`/`pass`；`summary` 为字符串；`thesis` 至少一项，每项有字符串 `text` 和非空 `refs`，否则 `malformed`。
3. `buy` 时 `levels` 含 `entry_low`、`entry_high`、`stop`、`target1`，每项 `value` 为正的有限数（数字或数字字符串）且 `refs` 非空，否则 `malformed`。
4. 任何 `refs` 中出现本次 propose 阶段未返回过的引用 → `invalid_citation`（整条作废）。
5. 每个价位的 `refs` 至少含一次价格类工具调用 → 否则 `invalid_citation`。
6. 价位四舍五入到 0.01 后满足 `stop < entry_low <= entry_high < target1`，且都在 T 日收盘价 × (1 ± 0.5) 之内 → 否则 `level_check`。

作废的建议不展示内容，只展示状态与原因（与解读的 `error` 一致）。

### 6.3 反方

反方结果 `{status, risks: [{text, refs}], dropped}`：`refs` 中有未返回过的引用（两个阶段的调用都算）的条目丢弃并计入 `dropped`；输出无法解析为 `malformed`；模型失败为 `error`。反方失败不使建议作废；输出中的任何价位或动作被忽略。

### 6.4 结果

| 状态 | 含义 | `reason` |
|---|---|---|
| `ok` | 建议买入，已生成 `agent` 计划 | — |
| `pass` | 建议不买入 | — |
| `rejected` | 校验未通过，整条作废 | `malformed` / `invalid_citation` / `level_check` / `tool_rounds_exceeded` |
| `error` | 运行失败 | `model_error` / `quota_exhausted` / `checkpoint_unavailable` |
| `skipped` | 未运行 | `disabled` / `not_configured` / `quota_exhausted` / `token_budget` / `stage_budget` / `max_attempts` |

中文：已关闭、未配置、额度用尽、超出 token 上限、超出时限、已达重试上限、输出格式无效、引用无效已作废、价位校验未通过、工具调用超限、模型调用失败、状态库未就绪。

### 6.5 提示词（`agent-1`）

propose：

```
你是美股波段交易研究员，为一个已触发规则的买入候选独立给出交易建议。
只能通过工具获取数据；工具只返回截止时间之前的数据，你不能指定日期。
每个工具结果带有 ref（如 call:3）。你写出的每个价位和每条理由都必须引用支撑它的 ref，只能引用本次真实返回过的 ref；引用不存在的 ref 会使整条建议作废。每个价位至少引用一次 get_price_bars 或 get_indicators 的结果。
计划在信号日后第 1 到第 3 个交易日内有效；到达 T1 后卖出一半，剩余按 3 倍 ATR 移动止损。不要考虑仓位大小。
完成后只输出一个 JSON 对象，不要输出其他文字：
{"action": "buy" 或 "pass", "summary": 一句话, "levels": {"entry_low": {"value": 数字, "refs": [...]}, "entry_high": {...}, "stop": {...}, "target1": {...}}, "thesis": [{"text": 字符串, "refs": [...]}]}
action 为 pass 时省略 levels。价位须满足 stop < entry_low <= entry_high < target1。
```

counter：

```
你是反方审查员。下面是一条买入建议及其引用。请找出反对这笔交易的证据，并核对建议中的引用和数字是否与工具结果一致。
可以调用工具补充数据；每条结论必须引用 ref，只能引用真实返回过的 ref。你不能修改价位或动作。
只输出一个 JSON 对象：{"risks": [{"text": 字符串, "refs": [...]}]}
```

## 7. Agent 计划

`ok` 的建议生成 `kind = 'agent'` 的计划，`plan_version = 'agent-1'`：

| 项 | 规则 |
|---|---|
| 价位 | 建议的四个价位（美分） |
| `ref_price`, `atr` | T 日收盘价与 ATR14，冻结（ATR 不可算时为 0，移动止损退化为保本） |
| 有效期、移动止损倍数 | 同 `plan-1` |
| 仓位 | 同 P1 §4.3，参考价取入场区间中点（同自定计划） |
| 状态 | 有效期内有财报 → `invalid / earnings_in_validity`；`no_room` → `invalid`；财报提示与环境提示同规则计划 |
| `inputs` | 同规则计划（K 线、财报、资金快照、敞口、`context`），另加 `agent_run_id`、`model` |

`agent` 计划与规则、自定计划一样进入计划影子跟踪（`plan-shadow-1`），与你的实际选择无关；周度复盘「计划影子」与月度复盘按 `kind` 分组时自然出现 `agent` 一组（不升 `calc_version`）。分池视角仍只计规则计划。

## 8. 批处理

### 8.1 位置与顺序

在规则循环（含规则计划与 Codex 解读）全部结束之后新增 **Agent 阶段**，推送之前：

1. 范围：本交易日 `proposed_action = buy`、无 `conflict` 标签、尚无决定的单元（与规则计划同一集合，规则计划因 ATR 跳过的也包括）。
2. 排序：主列表单元在前，扩展池在后；各自按标的字母序。
3. 某单元已有 `ok` / `pass` 运行 → 跳过（不重复生成）；已有 3 次运行 → 记 `skipped / max_attempts`（只记一次）；否则运行，`attempt` = 已有次数 + 1。
4. 批处理开始已超过 1500 秒 → 剩余单元 `skipped / stage_budget`。
5. 出现 `quota_exhausted` → 剩余单元 `skipped / quota_exhausted`。

每个被跳过的单元也写一行 `agent_runs`，保证「每个候选都有 Agent 建议或明确失败原因」。

### 8.2 运行报告新增

| 键 | 内容 |
|---|---|
| `agent` | `{'runs': {ticker: 'ok' / 'pass' / '<status>: <reason>'}, 'tokens': {'input', 'output'}, 'quota_exhausted': bool}` |
| `sources.agent` | 状态为 `error` 的标的 |
| `timings.agent` | Agent 阶段耗时（秒） |

## 9. 决定与纪律

- 采纳买入单元时，`plan_id` 可指向该单元 `ok` 的规则计划或 `ok` 的 `agent` 计划；二者都不可用时只能带 `own_plan`。其余校验同 P1 §5.3。
- 纪律指标的执行轨道自然取计划 `kind`，新增 `agent`。

## 10. 今日、推送与接口

### 10.1 今日

每个单元增加 `agent`：最新一次运行。`ok`/`pass` 时含建议、反方、计划、模型、token；其余只含状态、原因与时间。扩展池只读列表增加 Agent 状态。

### 10.2 推送

- 新计划段：每个主列表买入单元在规则计划之后加一行 Agent：
  - `ok`：`Agent（glm-5.3）买入 · 区间 a–b · 止损 s · T1 t · {股数} 股；{summary}；反方：{第一条风险}`，作废原因同规则计划写法。
  - `pass`：`Agent（glm-5.3）不建议买入；{summary}`。
  - 其他：`Agent 未给出建议（{原因中文}）`。
- 规则计划因 ATR 跳过而只有 Agent 计划的单元，也出现在新计划段。
- 概览增加 `Agent 建议 {ok} · 不建议 {pass} · 未给出 {n}`；额度用尽时加「⚠️ Agent 额度用尽，其余单元未运行」。
- 异常段增加数据源 `agent`（中文「Agent」）。
- 仍满足 Discord 限制；证据文案「证据：未验证」。

### 10.3 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/workbench/today` | 扩展：单元 `agent` |
| GET | `/api/workbench/agent-runs?since=` | 运行列表（不含工具返回值）与覆盖率：区间内每个买入单元是否有运行 |
| GET | `/api/workbench/agent-runs/{id}` | 单次运行与全部工具调用（参数、返回值、哈希），用于追溯 |
| POST | `/api/workbench/agent-runs` | 手动为单元 `{ticker, signal_date}` 运行一次；已有 `ok`/`pass` → 409；不是买入单元 → 400 |

命令行：`python -m investment_assistant.workbench.agent setup`（建检查点表）、`python -m investment_assistant.workbench.agent run --ticker X --date YYYY-MM-DD`（同手动接口）。

### 10.4 页面

- **今日**：计划卡片在规则计划旁显示「Agent 建议」：动作、价位、股数、理由（带引用编号）、反方、模型与版本；引用编号可展开查看对应工具调用；失败时显示原因与「重新运行 Agent」按钮。执行依据增加「Agent 建议」（Agent 计划为 `ok` 时可选）。
- **复盘**：计划影子与纪律中轨道名显示「Agent」。

## 11. 测试与交付

- **纯函数**：校验六步各自的失败；围栏与数字字符串；反方丢弃无效引用；Agent 计划价位、仓位与作废；额度错误识别与密钥脱敏。
- **工具层**：截止时间之后写入的 K 线、资讯不可见；越权标的与越界参数报错并记录；每次调用的哈希；`get_trigger` 不含计划价位。
- **图**：用脚本化的假模型（不联网）跑通 `ok`、`pass`、`rejected`（各原因）、工具超限、反方失败；`InMemorySaver` 替代检查点。
- **数据库**：一次性 PostgreSQL 执行迁移 019；`agent` 计划唯一；运行约束；三者同事务写入；`PostgresSaver.setup()` 在一次性 schema 中可用。
- **批处理**：排序、跳过已有结果、重试上限、时限、额度用尽后其余跳过且规则计划与推送不受影响、未配置不发请求。
- **推送与前端**：Agent 行三种写法、仅 Agent 计划的单元、Discord 限制；vitest 覆盖 Agent 建议块、引用展开、执行依据选项、重新运行按钮。
- **文档（DoD）**：更新 `docs/us-workbench.md`，新增「Agent 轨道」一节，写明手动入口、验证入口与验证示例。

真实库执行迁移 019、建检查点表、配置 `ZHIPU_API_KEY` 均单独确认；执行迁移前 `pg_dump` 备份。

## 12. 与现有能力的关系

| 现有能力 | 处理 |
|---|---|
| `rules.py`、`synthesis.py`、`plans.build_rule_plan` | 不变 |
| `briefs.py` / Codex 解读 | 不变，与 Agent 并存，P4 合并 |
| `plans.py` | 新增 `build_agent_plan` |
| `plan_shadow.py`、`review.py`、`monthly.py` | 不变；按 `kind` 分组时出现 `agent` |
| `ledger.py` | 采纳可指向 `agent` 计划 |
| `discipline.py` | 不变；轨道取 `kind` |
| `digest.py` / `templates.py` | 新增 Agent 行、概览计数、`agent` 异常源 |
| `macro.redact` | 复用于智谱密钥脱敏 |
| `hermes/deepseek_client.py` | 不变；Agent 不使用 |
