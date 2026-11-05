# P1 规则计划与推送 — 设计

日期：2026-10-23。基线 `main@4c72960`。本文是已确认的设计，不是实施计划；实施计划另写。
上位文档：[roadmap v2](../archive/v2/roadmap/roadmap.md#81-v2-路线2026-10-23) 的 P1。在[美股投研工作台设计](2026-10-23-us-research-workbench-design.md)之上扩展，原设计未改动的部分继续有效。

## 1. 目标与非目标

把工作台的决策单元升级为**带价位和仓位的交易计划**，每天推送到 Discord，并能按计划衡量执行纪律和月度收益。

已确认的前提：

| # | 决策 | 结论 |
|---|---|---|
| 1 | 到达 T1 后 | 卖出一半，剩余用移动止损（入场以来最高收盘 − 3 ATR，不低于入场价） |
| 2 | 已有 `position_drawdown` | 有计划的持仓用计划止损；无计划的持仓继续用 `position_drawdown` |
| 3 | 资金录入 | 月末按券商录总资金，出入金按笔录入 |
| 4 | 采纳时改价位 | 允许；自定价位存为新计划，纪律对照你确认的那份 |
| 5 | 无事日推送 | 照样推简报；休市推一行说明 |
| 6 | 财报临近 | 有效期内有财报则计划作废；之后 10 个交易日内有财报则提示；持仓股财报前一日提醒 |
| 7 | 计划存储 | 新建只追加的 `trade_plans` 表 |
| 8 | 解读模型 | P1 保持 Codex 解读不变 |

非目标：不接券商、不自动下单、不扩大观察名单（P2）、不引入 Agent 或 LangGraph（P3）、不改 Codex 解读的输入与提示词、不推送周度与月度复盘（P4）、不改写或回填旧表数据。Phase 0 正式 Gate 仍为 HOLD，每份计划标注规则证据状态为「未验证」。

## 2. 设计原则

沿用工作台原则：只追加不覆盖；按时点可还原；规则与公式在代码里且带版本；人在环路。新增两条：

1. **价位可重算。** 规则计划只由冻结在计划里的输入和公式版本决定；公式改动即发新 `plan_version`，旧计划不变。
2. **失败不冒充空。** 财报日期采集失败、缺资金快照、推送失败都显式记录，不写成「无财报」「收益 0」「已推送」。

## 3. 数据模型

迁移 `017_trade_plans.sql`，只新增，不改旧数据。所有时间戳为 `TIMESTAMPTZ`，`created_at` / `recorded_at` 由服务端生成。

### 3.1 `trade_plans` — 交易计划

| 字段 | 说明 |
|---|---|
| `id` | UUID |
| `ticker`, `signal_date` | 所属决策单元 |
| `kind` | `rule` / `own`（P3 增加 `agent`） |
| `based_on_id` | `own` 计划指向被修改的 `rule` 计划，可空 |
| `plan_version` | 公式版本，首版 `plan-1` |
| `ref_price`, `atr` | 参考价（T 日收盘）与 ATR14，生成时冻结 |
| `entry_low`, `entry_high`, `stop`, `target1` | 价位，美分精度 |
| `trail_atr_multiple` | 移动止损 ATR 倍数，默认 3 |
| `valid_from`, `valid_until` | 有效期（交易日） |
| `sizing` | JSONB：资金、资金快照日期与 id、风险比例、单票上限、总持仓上限、每股风险、建议股数、生效的上限、`status`（`sized` / `capped` / `no_equity`） |
| `status` | `ok` / `invalid` |
| `invalid_reasons`, `warnings` | TEXT[] |
| `inputs` | JSONB：K 线 id、财报日期记录 id、持仓敞口快照 |
| `request_id`, `request_hash` | 仅 `own` 计划，防重复提交 |
| `created_at` | 写入时间 |

约束：`rule` 计划在 `(ticker, signal_date, plan_version)` 上唯一（部分唯一索引）；`own` 计划的 `request_id` 唯一；价位满足 `stop < entry_low <= entry_high < target1`。重跑得到相同 `rule` 计划时返回已有记录；不同时不覆盖，写入运行报告的冲突列表。

### 3.2 `signal_decisions` 新增 `plan_id`

`plan_id UUID REFERENCES trade_plans(id)`，可空。旧行保持 NULL。校验见 §5.3。

### 3.3 `earnings_dates` — 下次财报日期

`id`、`ticker`、`earnings_date`（DATE，可空）、`status`（`known` / `unknown`）、`source`（默认 `yfinance`）、`fetched_at`、`created_at`。与该股上一条记录内容相同则不写，变化则追加。采集失败不写行。

### 3.4 `account_snapshots` — 资金快照

`id`、`request_id`、`request_hash`、`as_of`（DATE）、`equity`（> 0）、`note`、`recorded_at`、`supersedes_id`。更正写新行指向旧行；读取时只用未被取代的行。

### 3.5 `cash_flows` — 出入金

`id`、`request_id`、`request_hash`、`flow_date`、`amount`（≠ 0，入金为正）、`note`、`recorded_at`、`voided_at`、`void_reason`。只能作废，不能修改，约束同 `trades`。

### 3.6 `monthly_reviews` — 月度复盘

`id`、`month`（当月 1 日）、`calc_version`、`stats`（JSONB，生成时冻结）、`notes`、`supersedes_id`、`created_at`。

## 4. 计划生成

新增纯函数模块 `workbench/plans.py`。

### 4.1 哪些单元生成计划

只为 `proposed_action = buy` 且无 `conflict` 标签的单元生成 `rule` 计划。卖出、观察、冲突、回避单元不生成。单元已有决定或同版本 `rule` 计划已存在时跳过。

### 4.2 价位（`plan-1`）

| 项 | 公式 |
|---|---|
| ATR14 | Wilder 平滑；TR 用 `price_bars` 的 `high`、`low`、`close`（拆股调整、未分红调整） |
| 参考价 | T 日 `close` |
| 入场区间 | 参考价 ± 0.5 ATR |
| 止损 | 参考价 − 2 ATR |
| T1 | 参考价 + 4 ATR（2R） |
| 有效期 | `valid_from` = T+1，`valid_until` = T+3（交易日） |

价位四舍五入到 0.01。K 线不足以计算 ATR 时不生成计划，跳过原因写入运行报告。

### 4.3 仓位

账户参数放在配置文件新增的 `workbench` 段：`risk_pct`（默认 0.01）、`single_cap`（0.25）、`total_cap`（1.0）。

1. 资金：`as_of <= signal_date` 且 `recorded_at <= cutoff` 的最新有效快照。
2. 风险股数 = floor(资金 × `risk_pct` ÷ (参考价 − 止损))。
3. 单票上限股数 = floor((资金 × `single_cap` − 该股持仓按 T 日收盘的市值) ÷ `entry_high`)。
4. 总持仓上限股数 = floor((资金 × `total_cap` − 全部持仓按各自最近收盘的市值) ÷ `entry_high`)。
5. 建议股数取三者最小；被上限压低时 `sizing.status = capped`；结果 ≤ 0 时计划 `invalid`，原因 `no_room`。
6. 无资金快照时只给每股风险，`sizing.status = no_equity`，计划仍为 `ok`。

参数值冻结进 `sizing`；修改参数不需要新 `plan_version`。

### 4.4 财报

读 `cutoff` 之前该股最新的 `earnings_dates` 行：

| 情况 | 处理 |
|---|---|
| `earnings_date` 在 `valid_from`–`valid_until` | `invalid`，原因 `earnings_in_validity` |
| 在 `valid_until` 之后、T+10 交易日之内 | 提示 `earnings_within_10d` |
| 无记录、`unknown`，或日期已过 | 提示 `earnings_unknown` |

yfinance 只给日期、不给盘前盘后，按日期判断。`earnings_gap_up` 触发时下一次财报约在一个季度后，不受影响。

### 4.5 自定计划

采纳时随决定提交 `entry_low`、`entry_high`、`stop`、`target1`。校验价位顺序；仓位按 §4.3 计算；`own` 计划不会被判 `invalid`：`rule` 计划的作废原因以及 `no_room` 在 `own` 计划中都降为 `warnings`；`no_room` 时建议股数为 0。

## 5. 持仓止损与决定

### 5.1 持仓的计划

当前持仓中，最近一笔关联了 `plan_id` 的买入成交（经 `decision_id` → `signal_decisions.plan_id`）所对应的计划。没有这样的成交即为「无计划持仓」。

### 5.2 当前止损与新规则

- 开仓以来最高价未触及 T1：当前止损 = 计划 `stop`。
- 触及过 T1：当前止损 = max(开仓以来最高收盘 − `trail_atr_multiple` × 计划 `atr`, 持仓成本)。

价位遇拆股按 `split_factor` 换算到当前基础。

新增规则（`rule_version = '1'`，只对有计划的持仓运行）：

| 规则 | 动作 | 条件 |
|---|---|---|
| `plan_stop` | 卖出 | T 日最低价 ≤ 当前止损；提示核对券商止损单是否已成交 |
| `plan_target` | 卖出（一半） | 开仓以来最高价首次触及 T1；只触发一次 |

有计划的持仓不再运行 `position_drawdown`；无计划的持仓照旧。

### 5.3 决定的校验

- 采纳（`accept`）买入单元：存在 `ok` 的 `rule` 计划时，必须带 `plan_id` 指向它，或带 `own_plan`。
- `rule` 计划为 `invalid` 时，采纳只能带 `own_plan`。
- 带 `own_plan` 时，自定计划与决定在同一事务写入，决定的 `plan_id` 指向新计划。
- 否决和降级不需要计划。

## 6. 影子跟踪与纪律指标

### 6.1 计划影子跟踪（`plan-shadow-1`）

`rule` 与 `own` 计划分别模拟，与实际成交无关。实时计算，复盘生成时冻结。

| 环节 | 规则 |
|---|---|
| 入场 | `valid_from`–`valid_until` 内第一天 `[low, high]` 与入场区间相交：开盘在区间内按开盘价；开盘高于区间按 `entry_high`；开盘低于区间按 `entry_low` |
| 过期 | 有效期内未相交，状态 `expired`，计数不计 R |
| 止损 | 自成交当天起，`low ≤ 当前止损` 即离场；开盘已低于止损按开盘价 |
| T1 | `high ≥ target1` 首次发生时卖出一半，价格 max(开盘, T1) |
| 同日冲突 | 止损与 T1 同日触及，按止损先发生 |
| 移动止损 | 剩余一半：max(成交以来最高收盘 − 3 × 计划 ATR, 成交价)，收盘后更新，下一交易日生效 |
| 结果 | 每半 R = (出场价 − 成交价) ÷ (成交价 − 止损)，两半平均；另给收益率与同期 SPY 收益；未离场为 `open`，按最新收盘计浮动 R |
| 拆股 | 计划价位按 `split_factor` 换算 |

原有 5/10/20 日对照组（`review.shadow_outcome`）保留不变。

### 6.2 纪律指标

只针对实际成交，对照决定所确认的计划。

| 指标 | 判定 | 成熟时点 |
|---|---|---|
| 追高进场 | 买入价 > `entry_high` | 成交即判 |
| 过早止盈 | 卖出时持仓未触及 T1 且不是 `plan_stop` 后的卖出；卖出后 20 个交易日内最高价到达 T1 | 卖出后满 20 个交易日 |
| 死扛亏损 | `plan_stop` 触发后，至下一交易日收盘无卖出成交 | 下一交易日收盘后 |
| 计划外交易 | 买入成交无 `decision_id`，或决定无 `plan_id` | 成交即判 |

另给：计划遵守率（无上述问题的计划内买入 ÷ 计划内买入）；每笔实际 R = (卖出价 − 买入价) ÷ (买入价 − 计划止损)，按先进先出配对；每笔成交的执行轨道（`rule` / `own`）。

## 7. 批处理与推送

### 7.1 批处理顺序

1. 采集：日线、资讯、SEC（不变），新增下次财报日期。
2. 规则：原规则 + `plan_stop`、`plan_target`；有计划的持仓跳过 `position_drawdown`。
3. 合成决策单元（不变）。
4. 生成 `rule` 计划（新增）。
5. Codex 解读（不变，不含计划价位）。
6. 汇总推送内容并推送（新增）。

财报日期采集失败只记入运行报告，不写库。

批处理的标的集合 = 观察名单 ∪ 当前有持仓的股票，保证持仓止损和财报提醒有数据；10 只上限只检查观察名单。

### 7.2 推送内容

`run_task` 对 `workbench` 改为发送通知，沿用现有 `dispatch` 与全局渠道设置（当前为 Discord）。新增任务键 `workbench`：默认频道 `signals`，默认开启，可在「设置」中改频道或关闭。新增模板 `workbench_digest`，按段各发一条消息：

| 段 | 内容 |
|---|---|
| 概览 | 交易日、新计划数、持仓数、待处理数、异常数 |
| 新计划 | 每个标的一个字段：动作、规则与版本、入场区间、止损、T1、建议股数与风险金额、资金日期、有效期、财报提示、作废原因、证据状态、解读摘要与反证、系统链接 |
| 持仓 | 当前止损、距止损与 T1 的百分比、是否触及 T1、当日 `plan_stop` / `plan_target`、财报前一日提醒 |
| 待处理与异常 | 未决定单元、即将过期计划、各数据源采集失败 |

单条消息不超过 10 个 embed、6000 字符；超长截断并写「其余 N 条见系统」。系统链接前缀取配置 `workbench.public_url`（默认 `http://localhost:8787`）。无新计划也推送；定时运行遇纽约休市推一行「纽约休市，未运行」。

### 7.3 防重复与手动入口

- 只有定时运行自动推送；同一交易日已有成功推送（查运行报告）则不再推。
- 手动运行批处理默认不推送。「今日」页面新增「重新推送今日计划」，对应 `POST /api/workbench/push`，强制推送。
- 推送失败不使批处理失败；结果写入运行报告，下次推送附「上次推送失败」提示。

## 8. 复盘

### 8.1 周度复盘

新增「计划影子结果」与「纪律」两个视角，`calc_version` 升为 `review-2`。已生成的快照不变。

### 8.2 月度复盘

手动生成并冻结，更正时新快照指向旧快照。P1 不推送（P4）。

| 项 | 计算 |
|---|---|
| 月度收益率 | (本月最后快照 − 上月最后快照 − 两快照日期之间的净出入金) ÷ 上月最后快照；任一端缺快照则显示「缺资金快照，无法计算」 |
| 分档 | >20% 非常优秀、10–20% 优秀、0–10% 可行、<0 需关注；阈值在配置 `workbench.return_tiers` |
| SPY | 两快照日期之间 SPY 的 `adj_close` 收益与超额 |
| 流程复查 | 最近 3 个月复合收益 < 0 且跑输同期 SPY 则标记；不足 3 个月显示「样本不足」 |
| 敏感性表 | 当月已平仓交易的实际 R，按 0.5% / 1% / 1.5% / 2%：收益 ≈ ΣR × 比例，回撤 = 按平仓顺序累计 R 的最大回落 × 比例；页面注明为近似 |
| 其他 | 当月纪律指标、`rule` 与 `own` 计划影子结果、计数 |

## 9. 接口与页面

### 9.1 接口（`/api/workbench`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/today` | 扩展：单元附计划，新增持仓止损区块 |
| POST | `/decisions` | 支持 `plan_id` 或 `own_plan` |
| POST | `/push` | 强制推送当日计划 |
| GET / POST | `/account/snapshots` | 资金快照列表与录入（含更正） |
| GET / POST | `/account/flows` | 出入金列表与录入 |
| POST | `/account/flows/{id}/void` | 作废出入金 |
| GET | `/plans/shadow` | 计划影子结果 |
| GET | `/discipline` | 纪律指标 |
| GET / POST | `/monthly-reviews` | 月度复盘列表与生成 |

数据库未就绪返回 503，校验失败 400，状态冲突 409，与现有约定一致。

### 9.2 页面

- **今日**：单元卡片显示计划（价位、股数、提示、作废原因）；确认表单增加「执行依据：规则计划 / 自定价位」，规则计划作废时只能自定；新增「持仓」区块；新增「重新推送今日计划」按钮。
- **复盘**：在「交易与持仓」「周度复盘」之外新增「月度复盘」与「资金」页签；资金页签录入与作废快照、出入金，并只读显示风险参数。

## 10. 测试与交付

- **纯函数**：ATR；价位取整；仓位三上限与 `no_equity`、`no_room`；财报三种情况；影子跟踪边界（跳空穿止损、同日止损与 T1、三种入场、过期、拆股、移动止损保本下限）；四个纪律指标及未成熟；月度收益缺快照；敏感性表。
- **数据库**：一次性 PostgreSQL（端口 55433，测试后删除）执行迁移 017；验证只追加、部分唯一索引、冲突返回、`request_id` 重放。
- **推送**：模板不超过 Discord 限制、截断、防重复、失败写入报告、休市消息。
- **前端**：vitest 覆盖计划卡片、自定价位表单校验、资金页签。
- **文档（DoD）**：更新 `docs/us-workbench.md`，写明各功能的手动入口、验证入口与验证示例。

真实库执行迁移 017、开启推送前单独确认，执行前 `pg_dump` 备份。

## 11. 与现有能力的关系

| 现有能力 | 处理 |
|---|---|
| `rules.py` | 新增 `plan_stop`、`plan_target`；`position_drawdown` 仅对无计划持仓运行；原规则逻辑与版本不变 |
| `synthesis.py` | 不变 |
| `briefs.py` / Codex | 不变 |
| `review.py` | 新增计划影子与纪律视角，`calc_version` 升为 `review-2`；原对照组不变 |
| `ledger.py` | 决定支持 `plan_id` / `own_plan`；成交不变 |
| `positions.py` | 不变；持仓计划由成交关联推导 |
| `notify` | 新增 `workbench_digest` 模板与 `workbench` 任务键；支持一次任务发多条消息 |
| `tasks/workbench.py` | 定时运行开启推送，含防重复 |
| 配置 | 新增 `workbench` 段：`risk_pct`、`single_cap`、`total_cap`、`public_url`、`return_tiers` |
