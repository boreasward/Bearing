# 复盘

按计划和结果复盘：规则与 Agent 各自按自己的价位做影子跟踪，再和你的实际执行并列比较；另外统计纪律指标、周度和月度收益，按周、按月推送摘要。样本小、信号同日重叠，所以只给计数、均值、中位数和分布，不宣称任何规则或 Agent 有效。

设计：[P1](../specs/2026-10-23-p1-rule-plans-and-push-design.md)（影子、纪律、月度收益）、[P4 复盘扩展](../specs/2026-10-25-p4-review-expansion-design.md)（三方对比、推送）、[v2.5 计划时间线 §9](../specs/2026-10-28-v2.5-plan-timeline-design.md#9-影子跟踪与三方对比)（每版影子、三方对比取决定时看到的版本）、[v2.7 我的成长闭环](../specs/2026-10-28-v2.7-growth-loop-design.md)（复盘卡、周度「你学到了什么」）。

## v2.15 优化提案衔接（待发布与验收）

合并实施图 daily-3 / stages-6 将 retro 顺延为第 12 节点，后接第 13 节点 propose；旧 stages-5 仍为下文的 11 行。影子、纪律、tracks-2、review-8、monthly-5 的公式及冻结快照保持原口径，策略 / 指标仍 Gate HOLD。

- 优化层按纽约周五 session（默认北京周六运行）汇总近 7 日采集失败 / Agent 质量、近 30 日同 scope 重复教训、待验收版本与主指标复查信号，生成至多 3 条 [路线提案](roadmap-in-system.md)。复盘卡草稿作为优化证据不等于已采纳知识。
- O2 的 `counter_followed_by_successful_revision` 是「运行级后续修订比例，不证明逐条反方意见被采纳或因果关系」，不能写成反方意见已被采纳率。主指标仍按 monthly-5，端点不全明确缺失，不造零。
- propose 在 push、retro 之后，当次周度消息已经生成；提案提示进入**下一次**周度复盘推送，末尾「本周提案 N 条，到 roadmap 页查看」，不声称同次已发。
- 提案证据显示生成时冻结 `agent_runs.output.evidence_pack`，历史缺包明确缺记录。批准前无版本 / brief 变更；approved 也不等于上线，brief error / pending 要真实读回。

待执行核对：周五 session 提案与非提案日跳过、下一次周度提示、证据原行与历史包、批准前不变；综合核对为 `python -m investment_assistant.workflow check`。上线前规则检查入口继续为 `python -m investment_assistant.optimize.backcheck`（1D 搬迁后的路径），相关命令本轮未执行。

## 口径

成交与资金表单已迁入[账户页](accounts.md#页面)。纪律与轨道对比可通过 `account_id` 查询指定账户，默认真实账户；每次计算只取该账户的成交、FIFO 批次与持仓状态，计算公式不变。模拟持仓按冻结入场计划判断，不用真实账户的全局链修订。模拟账户的 `discipline-2` 只按冻结计划与本账户持仓状态判定，不读链头和 `plan_stop` 事件，因此「早卖」「死扛」的输入与真实账户不同。周月快照、复盘卡与推送仍默认真实账户，组合 `portfolio` 运行不进入单元覆盖率与计划三轨样本。

### 实际交易

先进先出配对。买入费用计入成本，卖出费用按数量分摊；跨拆股时按拆股比例换算。每笔交易显示持有交易日数、盈亏、同期 SPY、最大浮盈和最大浮亏。

### 对照组

全部单元（`review-8` 起；`review-7` 及更早只算已决定的单元，包括被否决的和观察的）都假设在下一交易日开盘入场，统计 5、10、20 个交易日相对 SPY 的超额收益。卖出单元取反（避开的跌幅），观察单元只记录走势。窗口没走完的显示「未成熟」，不拿部分窗口代替。

### 计划影子 `plan-shadow-2` / `plan-shadow-1`

`plan-shadow-1` 按价位逐日独立模拟规则计划、Agent 计划和自定计划，结果以 R 计：有三种入场方式；止损和 T1 在同一天都触及时，按先触止损算；移动止损有保本下限；跳空穿过止损时按开盘价成交。影子结果不受你实际选择的影响。

**`plan-shadow-2`（v2.9）**用于带执行字段的计划（`plan-2`、`agent-3`、v2.9 起的自定计划）：入场按计划的入场类型与开盘三种情形回放（开盘 ≤ 止损当天起作废，状态 `voided`，计 0R、汇总里单列）；成交后持有满最长持有天数而仍没有一个收盘到过成交价加 1R 时，在次一交易日开盘退出（`time_stop`，T1 之后不再适用）。同一份计划**同时**算 `plan-shadow-1` 作反事实列（无时间止损、旧入场规则）。没有执行字段的旧计划只算 `plan-shadow-1`。`GET /api/workbench/plans/shadow` 每行另带 `shadow_version`、带执行字段的计划另带 `shadow_v1`，响应顶层 `shadow_version = plan-shadow-2`，`agent_summary` 另有 `counterfactual: {v1, v2}`（同一批 Agent 计划在两种口径下的汇总，`v2` 另给 `voided` 计数）。周度 / 月度快照的计划影子：`plans` 列出全部计划（新口径用原键，旧计划用带口径名的键），`plans_v1`（plan-shadow-1）、`plans_counterfactual` 另给，两种口径不混算。

`agent-2` 计划链的每一版都是一份计划、各自模拟（修订版的有效期从复查日次一交易日开始，所以只可能在生成之后成交）。`GET /api/workbench/plans/shadow` 每行带 `chain`：`{version_no, origin, is_head, supersedes_plan_id}`（`agent-1`、规则与自定计划为 `null`）；响应另有 `agent_summary: {first, latest}`——`first` 每条链只算首版（`agent-1` 计划算自己的首版），`latest` 每条链只算当前最新版（`agent-1` 计划同样算自己）；每项给 `count`、`determined`（有 R 的份数）、`expired`、`r_mean`、`r_median`，有 R 的不足 5 份时均值与中位数为空。

### 纪律 `discipline-2`

`discipline-2`（v2.9）按成交自己的 `plan_id` 找计划（旧成交再看旧决定的计划，口径同 `discipline-1`）：追高对照**成交时关联的那一版**；过早止盈对照卖出时链的最新版 T1；死扛亏损对照每日持仓状态里的有效止损（当天最低价触及后下一交易日仍未卖出）；计划外 = 没有关联计划，或成交日在关联版本有效期外（`out_of_validity`，该笔不再判其它三项）。不新增第五项。

| 坏习惯 | 判定 |
| --- | --- |
| 追高进场 | 成交价高于计划入场区间上限 |
| 过早止盈 | 持有期内未到 T1 就卖出，之后价格到达 T1 |
| 死扛亏损 | 收盘跌破止损后，下一交易日仍未卖出 |
| 计划外交易 | 成交没有关联任何计划，或成交日在关联版本的有效期外（`discipline-2`） |

### 三方对比 `tracks-2`（`tracks-1` 是旧行的口径）

对同一批买入单元，把规则、Agent、实际三条轨道的结果并列，R 都按各自计划的止损计算。单元集合 = 窗口内有规则计划或有 propose 运行的单元。每条轨道的状态分为：`missing`、`not_executable`（0R）、`pending`（不计入均值）、`expired`（0R）、`open`（浮动 R）、`closed`、`pass`（0R）、`not_executed`（0R）、`no_plan`。实际轨道另外记录 `plan_kind`（照规则 / 照 Agent / 自定）。汇总时把「没算出来」和「0R」分开统计，均值只用已确定的值。每一行的计划、运行、决定、成交 id 都能追溯到对应接口。

`tracks-2`（v2.9）：Agent 轨道取第一笔关联买入所关联的版本（`version_basis = fill`），旧决定记了版本的取 `decision`，都没有取首版 `first`；被并入同标的另一条活跃链的单元，Agent 轨道为 `merged`（状态「并入其他链」，不计入均值、汇总里单列 `merged` 计数）；实际轨道的计划与 `plan_kind` 取成交关联的计划（没有关联成交 `not_executed`，0R；有买入但没有关联计划 `no_plan`）；开盘作废的影子计 0R（状态 `voided`）。下面是 v2.5 起的 `tracks-1` 口径，旧快照照旧显示：Agent 轨道取「你决定时看到的那一版」：决定记了 `agent_plan_id` → 就是这一版（`version_basis = decision`）；v2.5 之前的决定没有记 → 该单元的 `agent-1` 计划，没有则首版（`first`）；未决定 → 首版（`first`），不拿事后修订的版本美化结果。`run_id` 是生成这一版的运行。行上新增 `version_no`（`agent-1` 为空）与 `version_basis`（没有 Agent 计划时为空）；`agent-1` 单元其余字段与 v2.5 之前完全相同。

### 周度复盘 `review-8`

`review-8`（v2.9，D30）：`units.decided / undecided` 改为 `units.executed / not_executed`（单元集合是**全部单元**，D26）；`by_decision` 改为 `by_execution`（已采纳 / 有效期外 / 未执行 / 计划外四组，计划外一组只有计数）；执行偏差的延迟改为「所关联的计划版本生成到成交」；`unsignaled_trades` 改为 `unplanned_trades`（没有关联任何计划的买入）；对照组按全部单元；`learning.cards[].decision_text` 改为 `execution_text`（旧卡显示它的旧决定文案）；纪律为 `discipline-2`，三方对比为 `tracks-2`，计划影子：`plans` 列出本周全部计划，带执行字段的（`plan-shadow-2`）用原来的键，旧计划（`plan-shadow-1`）用「规则（旧计划 · plan-shadow-1）」这类带口径名的键，两种口径各自汇总、不混算，页面逐行列出；`plans_v1` / `plans_counterfactual` / `shadow_versions` 同一拆分供机器读取；分池 `by_pool` 只含 `plan-shadow-2` 的计划，旧计划另放 `by_pool_v1`；非买入单元（卖出 / 观察 / 冲突）没有执行状态（`execution = not_applicable`），不进 `units` 与 `by_execution`，仍进 `by_rule`；`execution[]` 每笔成交只归一个单元（链所在单元优先），自定计划的 `delay_hours` 为空。`review-7` 及更早的快照不重算，页面按 `calc_version` 分支显示（周度推送的概览同样分支）。

以下是 `review-7` 起的口径：从按规则、按决定、执行偏差、无信号交易、计划影子、纪律、分池几个视角统计，并冻结一份当期三方对比。计划影子按种类汇总：`agent` 每条链只算首版（`agent-1` 计划算自己的首版，页面标「Agent（首版）」），`agent_latest` 是快照生成时每条链的最新版（页面标「Agent（最新版）」），避免样本被修订次数放大（`review-5` 及更早的快照里 `agent` 是每一版都算，页面仍标「Agent」，已冻结的快照不重算）。分池按三分类：`core`（核心）、`screen`（筛选，`source = screen` 的计划不论是否进了主列表）、`extended`（扩展）；旧计划的 context 没有来源时按分层归池。快照生成后就冻结；要更正，就对同一周重新生成，新快照指向旧快照。

`review-7`（v2.7）起快照的 `stats` 多一个 `learning` 键，见下面「周度「你学到了什么」」；`review-6` 及更早的快照没有这个键，页面明说，不重算。

### 月度复盘 `monthly-5`

`monthly-5`（v2.11）已开账真实账户使用权益账本计算本月及前三个月收益，端点为交易日 `session`，扣除区间净出入金；尚未开账、缺必要权益端点，或任一端点最新行存在缺失 / 陈旧行情（`detail.bars_missing` / `detail.stale` 非空）时沿用资金快照算法并标明 `source = snapshot`，账本来源标 `ledger`；回退后仍缺快照时显示缺失原因。旧 `monthly-4` 及更早的冻结快照不重算。

`monthly-4`（v2.9）冻结 `tracks-2`、`discipline-2`，计划影子分 `plans` / `plans_v1` / `plans_counterfactual` 三块并附 `shadow_versions`；`monthly-3` 及更早的快照不重算。

- 未开账的兼容口径：月度收益 =（本月末资金快照 − 上月末资金快照 − 区间净出入金）÷ 上月末资金快照。任一端缺快照时，显示「缺资金快照，无法计算」。
- 分档：> 20% 非常优秀，10%–20% 优秀，0%–10% 可行，< 0 需关注；同时列出同期 SPY 和超额收益。
- 流程复查：连续 3 个月的复合收益 < 0，**且**跑输 SPY 复合收益，才标记，并附逐月明细。
- 敏感性表：用当月已平仓交易的实际 R，按单笔风险 0.5% / 1% / 1.5% / 2% 重算收益，按平仓时间排序并注明笔数。
- 另外冻结一份当期三方对比、纪律和计划影子（`monthly-4` 起见上）；计划影子的 `agent` / `agent_latest` 口径同周度（`monthly-2` 及更早的快照不重算，页面仍标「Agent」）。

资金快照按券商月末总资金录入，更正时必须指向当前有效的那份快照；出入金按笔录入（入金为正），录错只能作废。录入入口移到真实账户概览；开账后快照用于只读对账，不覆盖账本权益，见[账户与模拟交易](accounts.md)。

## 复盘卡

一笔交易平仓、或你决定过的一份计划结束（止损、到 T1、过期未成交、作废）之后，每日图的第 11 个节点 `retro`（见 [每日计划](daily-plan.md#每日流程)）检测到这个结束事件，把当时的事实**冻结**进一张卡，Agent 只把这些事实讲成一段话并起草 0–3 条教训，你审定后教训才进入「我的知识」（[知识层 · 我的知识](knowledge.md#我的知识)）。设计：[v2.7 §4](../specs/2026-10-28-v2.7-growth-loop-design.md#4-复盘卡)、[v2.9 D29](../specs/2026-10-30-v2.9-executable-plans-design.md)，版本 `retro-2`（对象、事实与判定）、`agent-retro-2`（起草；「你的决定」改为「你的执行」，输出结构与校验不变）；v2.9 之前建的 `retro-1` / `agent-retro-1` 卡原样保留、原样显示（卡的版本决定它的事实键、事实文本和重新起草用的提示词）。

- **对象**（两类，`retro-2`）：链或单元——有未作废的关联买入成交的（`trades.plan_id`；关联到被并入单元的规则基线也算这条链；旧成交走旧决定），或者你给它写过有效备注而没有成交的；计划外成交——没有关联任何计划（也没有旧决定）的未作废买入成交，每笔一张。没有成交、也没写过备注的链不出卡（`retro-1` 的对象是「做过决定」的单元）。
- **结束事件**（纯函数 `retro.end_event`）：有买入成交时只看平仓——这些买入的 FIFO 批次全部平掉才结束（`position_closed`，结束日是平掉最后一批的那笔卖出的纽约日期），持仓期间链被作废或计划过期**不**算结束；没有买入成交时，取参考计划上最早的一个：链被复查作废或参考计划按规则作废（`invalidated`）、影子第一条腿是止损 / 移动止损（`stop`）、是 T1（`target`）、影子 `expired`（`expired`，结束日为计划有效期止），同一天按 作废 → 止损 → 到 T1 → 过期 排序。参考计划（`retro-2` 先看成交自己的 `plan_id`，再走下面的旧规则）：没有买入成交时按「决定执行的计划 → 决定时看到的 Agent 版本 → 规则计划（`plan-2` 优先于 `plan-1`）→ `agent-1` 计划」取第一个存在的；有持仓时先取最近一笔买入成交关联的计划，旧成交只认决定执行的那一份（只看 `signal_decisions.plan_id`，不走上面的回退）——先取买入成交挂靠的那条决定的计划（几笔买入取最近一笔带计划的），没有再取决定链上最近一条带计划的决定，所以「采纳并买入、后来改成否决」的单元仍以当初采纳的计划出卡。整条链都没有 `plan_id` 的持仓单元不出卡，只在它平仓落在检测窗口内时计入阶段 `detail.no_plan`（不是每天对所有未平仓单元都计），`retro_cards check` 把它单独列成「无参考计划」并判为不通过。只认结束日在最近 5 个交易日内（含当日）的事件；更早的用 `retro_cards detect --since D` 手动补。平仓方式 `exit_basis` 只描述（按止损 / 到 T1 后 / 自行卖出），不是纪律判定。
- **同一事件一张卡**：`review_cards.event_key` 唯一（单元无持仓结束 `unit:<标的>:<信号日>:ended`；单元平仓 `unit:<标的>:<信号日>:closed:<最后一笔卖出 id>`；计划外成交 `trade:<买入成交 id>`），插入 `ON CONFLICT DO NOTHING`，同一 session 重跑不重复建卡。
- **冻结的事实**（`retro-1` 十个 `retro.FACT_KEYS`，`retro-2` 十一个 `retro.FACT_KEYS_V2`：`decision` 换成 `execution`——状态、照什么执行、首笔成交、建卡时有效的备注、旧决定原样放在 `legacy_decision`，另加 `holding`——每日持仓状态摘要：行数、最长持有、退出指令的日期与原因、实际卖出日与间隔；建卡时一次算好、之后不重算）：`subject`、`plans`（各份计划当时的价位、有效期、截止时间与角色）、`bars_after`（之后的日线摘要与每份计划的首次触及日期，拆股基准同 `plan_shadow`）、`shadow`、`tracks`（与「轨道对比」同一个函数 `service._track_rows` 的同一单元行，原样取 `rule` / `agent` / `actual`）、`decision`、`trades`、`discipline`（直接用 `discipline.evaluate`，未到判定期的记「待定」）、`reviews`、`knowledge`。卡页另显示「现在的判定」：按当前日期对同一批成交只读重算一次，与冻结结果不同时并列。
- **起草**（`agent-retro-1`，单阶段）：每次批处理对没有成功草稿的卡按结束日升序起草，**至多 5 张**（`RETRO_MAX_DRAFTS`），失败 3 次（`rejected` / `error`）后卡成为 `draft_failed`，不再算「需要起草」（不进阶段输入、Agent 不可用时也不降级，只在第 3 次失败的那次运行里报一次 `max_attempts`），等手动重新起草或直接审定；闸门与 Agent 阶段相同（超时限、Agent 不可用、本次已遇额度用尽、当日 token 达上限）。教训必须写适用范围与反例。起草结果通过校验才写草稿；每次尝试写一行 `review_card_drafts`（跳过也写一行原因，原因没变就不重复写）。写入时持卡级锁（与审定同一把）并重新核对：模型运行期间这张卡已被审定、或已有另一份成功草稿时，运行记录照留，草稿行记为 `rejected`（原因 `card_reviewed` / `draft_exists`），不写教训草稿；并发的两次起草各得不同的尝试序号。叙述的每一句都要引用 `fact:<键>`、本次工具调用、本次读到的概念卡或我的知识。
- **审定**（一次，`POST /api/workbench/review-cards/{id}/verdict`）：每条教训草稿「采纳（可改）/ 不采纳」且必须全部出现，可另写新教训，可写 0–2000 字「我的复盘」；同一事务写审定记录、每稿一行决定、采纳的教训（含另写的）成为新条目的第 1 版；二次审定 409 `card_reviewed`；没有成功草稿的卡也可以只写复盘或另写教训。卡状态由记录推出：`reviewed` / `to_review` / `draft_failed` / `drafting`。
- **重新起草**（`POST …/redraft`）：前置检查同步执行（卡不存在 404、已有成功草稿 409 `draft_exists`、已审定 409 `card_reviewed`、Agent 不可用 409 `agent_unavailable: <原因>`、当日 token 达上限 409 `token_budget`，迁移 027 未执行 503），通过后后台起草，返回 `{run_id, status: pending}`，用 `GET /api/runs/{run_id}` 查结果；不受 3 次上限。
- **页面**：「复盘」的第二个页签「复盘卡」（`#review/cards`，页签带待审定数；可按状态、标的筛选），卡页 `#review/cards/<id>` 依次是页头、预期与实际（表格来自冻结事实，叙述的引用可点开）、三条轨道、你的决定与纪律（检测时 / 现在）、教训草稿与审定、起草过程、重新起草。
- **接口**：`GET /api/workbench/review-cards?status=&ticker=&since=`（卡列表，带按状态的 `counts`；每行与卡页的 `card`、周度 `learning.cards[]` 另有布尔 `filled` = 有未作废买入成交，页面据此区分「未成交」与「无 R（计划外）」）、`GET /api/workbench/review-cards/{id}`（`card`、`facts`、`status`、`drafts`、`lesson_drafts`、`verdict`、`discipline_now`、`discussions`）、上面的 `verdict` 与 `redraft`。迁移 027 未执行时这些接口 503「迁移 027 未执行」，页面显示同一句。
- **命令行** `python -m investment_assistant.review.retro_cards`：`detect [--since D]`（检测并建卡，不起草）、`draft --card ID`（同步起草一次）、`show --card ID`（只读）、`check --since D [--until D]`（只读）：窗口内每个按 §4.1 现算出的结束事件恰有一张卡，每张卡的事实键齐全（按卡自己的版本），`facts.tracks` 与按冻结当时的交易日和已知日线重算的行相同（`retro-2` 卡对 `tracks-2`，`retro-1` 卡对 `tracks-1`）（成交作废等账本的后来变化列为「事实已变化」，不算失败；影子在平仓之后继续走不算变化）；平仓落在窗口内、决定链上却没有任何计划的持仓单元单独列为「无参考计划」；缺卡、事实不全或有无参考计划的单元退出码 1，参数错误、数据库不可用或迁移 027 未执行退出码 2。
- **怎么验证一次**：手动运行批处理后，`stage_runs` 该 `run_id` 有 11 行（`stages-5`），`retro` 行的 `output_count` 等于本次新成功的 `review_card_drafts` 数；`retro_cards check --since <首次批处理交易日 S 之前的第 4 个交易日（= S−4，检测窗口起点）>` 通过；任选一张卡，`facts.tracks` 与「复盘 → 轨道对比」同一单元逐项一致，纪律与 `GET /api/workbench/discipline` 一致。
- **已知限制**：未成交的链只有你写过备注才出卡，没写备注的过期 / 作废链在「标的」页计划历史里读；无链持仓与同标的新入场链并存时，`holding_status` 按标的一行、链上的买入按链，两边股数可能不同；分次买入使加权成本上移后 `reached_1r` 可能由真变假，时间止损重新生效（v2.9 R19）；冻结事实不随后来的更正变化（建卡后成交被作废时，卡页与 `check` 标「事实已变化」，不重建；同单元若因此变成另一种结束，可能出第二张卡）；未决定的单元没有卡，上线前的旧交易默认没有卡；纪律的「待定」可能很久，以「现在的判定」为准。证据状态仍是「未验证」。

## 周度「你学到了什么」

`review-7` 的周度快照新增 `stats.learning`（`review.learning_stats`，在 `generate_weekly` 的同一个只读快照里算）：`discipline`（本周结束的卡的冻结纪律合计：`cards`、`chase`、`early_take`、`held_loser`、`unplanned`、`pending`）、`cards`（本周结束的卡按结束日排列的摘要）连同 `counts`（`total`、`reviewed`、`to_review`、`drafting`、`draft_failed`）、`knowledge`（本周至少有一个样本结果落在这一周的我的知识条目：`ref`、`title`、`new_outcomes`、`sample`，`sample` 与表现回看同口径，有结果的不足 5 个不给均值）。迁移 027 未执行时生成的快照带全零的 `learning`。页面「复盘 → 周度复盘」卡片多一段「你学到了什么」。

推送（`review_digest.weekly` 新增 `learning` 键，`notify.templates.workbench_weekly` 在「纪律」之后、「说明」之前多一个字段「你学到了什么」）：第一行 `纪律（本周结束的 n 笔）：追高 … · 过早止盈 … · 死扛亏损 … · 计划外 … · 待定 …`，第二行 `复盘卡 n 张（已审定 … · 待审定 …）`，再是至多 5 张卡各一行（`标的 结束类型 · 实际 … · 执行文案`（`review-7` 及更早的快照是决定文案），实际写 R；买过但没有 R（计划外成交没有止损可算风险）写「无 R（计划外）」，没买过写「未成交」；超出写 `…另有 n 张`），然后 `被检验的知识 k 条` 与至多 3 行（`know:<id>@<版本> 标题 · 样本 n（均值 … / 少于 5 个不给均值）`）；三项都为空时只写一行「本周没有结束的交易，也没有被检验的知识」；字段超过 1024 字从后面截断并加 `…`。

## 复盘推送

每日工作流的 `retro` 节点（v2.8 起；北京时间周二至周六 11:30，默认 `signals` 频道，推送类别仍是 `workbench_review`）每个运行日检查一次（休市日也检查；手动运行只生成不推送）：

- **周度**：每周最后一个交易日收盘后的首次运行，冻结并推送该周快照。
- **月度**：上月有完整账本月端点或月末资金快照、且还没推送过时，生成并推送。缺快照时，从次月第一个周六起提醒一次；工作台启用之前的月份不提醒（`monthly.action` 记为 `before_start`）。
- 推送成功后不再重复；`send_failed` 或 `task_disabled` 时下一次运行重试；每次尝试都写一行 `review_pushes`。手动推送会强制重发。

## 入口与验证

- 页面「复盘」：复盘卡、对照组、周度复盘、月度复盘、纪律、轨道对比、上线前检查（见 [观察池与市场环境](pool-and-context.md)）；周度和月度卡片上都有「推送到 Discord」按钮。
- API：`GET /api/workbench/review/tracks?since=&until=`（默认最近 90 天，不冻结）；`GET/POST /api/workbench/monthly-reviews`；`GET /api/workbench/plans/shadow`；`GET /api/workbench/discipline`；`GET/POST /api/workbench/account/snapshots`、`GET/POST /api/workbench/account/flows`、`POST /api/workbench/account/flows/{id}/void`；`GET/POST /api/workbench/review-pushes`（POST body 为 `kind` 与 `period`；该周期没有快照返回 404，period 不是周一或每月 1 日返回 400）；（原 `POST /api/jobs/workbench_review/run` 已随任务合并删除，手动运行是 `POST /api/workbench/run`）；周 / 月复盘的手动生成与推送接口不变。
- 验证一次：周六之后的第一个运行日（定时），`retro` 节点行的 `detail.weekly.action == "generated"` 且 `push.sent == true`（手动运行只生成：`push == "manual_run"`）（消息里有「你学到了什么」字段）；同一天再跑一次，`weekly.action == "already_sent"`。录入 8-31 和 9-30 两份资金快照后生成 9 月月度复盘，页面显示「月度收益 +x.xx% · 档位 · SPY …」。
