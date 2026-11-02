---
title: 系统每天怎么跑
version: 4
---

## 系统做什么、不做什么

系统每个交易日收盘后（定时任务 `daily`，北京时间周二至周六11:30）按 `daily-3` / `stages-6` 跑十三节点：SEC监控 → 数据采集 → 筛选 → 市场环境 → 信号 → 规则计划 → Agent建议 → 每日复查 → 组合检查 → 账户 → 推送 → 复盘 → 优化提案。一般未处理异常等60秒重试一次，再失败停链，可从检查点续跑；各节点已处理的降级按下文继续，见[每日工作流](concepts/system.daily_workflow.md)。旧运行按旧版本读取，不补造新节点。

**做什么**：出研究候选和计划，每个价位都能追溯到规则、数据和它的输入。**你决定、你下单、你录入成交**：系统不接券商，不会替你买卖，也不会自动调整你的持仓。

**不做什么**：它不预测行情，也不保证任何结果。更要紧的是，所有规则和 Agent 都**未经验证**——Phase 0 的正式结论是暂不放行（Gate HOLD），所以每份计划都带着"证据：未验证"。推导一致、数据完整、Agent 默认展示在前，都不等于有效，详见 [证据状态「未验证」](concepts/system.gate_hold.md)。

三条贯穿全系统的口径：规则、计划和 Agent 的数据类工具只读[截止时间](concepts/system.cutoff.md)之前已入库的数据，所以能还原"当时知道什么"（不是严格的历史回测；`get_trigger`、`get_position` 读当日候选与持仓记录，不按截止时间过滤）；业务记录[只追加](concepts/system.append_only.md)，更正是新记录指向旧记录，错误不会被悄悄抹掉；每套口径都有版本号，旧记录保留它当时的版本。

数据、推导、分析、执行、优化各有职责，[分层](concepts/system.layers.md)不改公式；[功能开关](concepts/system.feature_flags.md)在运行开始冻结，续跑沿用。portfolio_manager 关闭时跳过整个组合节点，今日显示未计算；开启且已产出硬结果后 PM 失败，才保留硬结果并降级。手动研究只产档案，优化提案获批前不创建版本或 brief，所有新增计算与模型判断仍是 Gate HOLD。

读这一页时，每一步都有「当天这一步」的链接，点开会回到「今日」顶部的运行一览，看当天这一步实际做了什么、跳过了什么。

## ① SEC 监控

**做什么**：每天第一步先看 SEC。扫描你的 SEC 观察名单里各公司的新申报（10-Q、10-K，以及主文含 Item 2.02 或带 EX-99.1 附件的 8-K），把原文归档到本地、提取正文里的营收、毛利、营业利润、利润率和摊薄 EPS（带期间、单位和原文位置），再以 Bot 身份直接发到指定的 Discord 频道；已经通知过的申报不会重发。这一步属于 [SEC 申报](concepts/family.sec_filings.md)这个数据族，与后面的计划判断互不依赖，所以放在最前面。

**读什么、写什么**：读 SEC EDGAR，写归档文件、申报索引和通知记录；正文处理走[SEC 正文](concepts/family.sec_text.md)适配器，记录 `family=sec_text, scope=sec_monitor`。各入口共享有效 `config.filings.output_dir` 与其中 `.budgets` 的下载前预留额度，每标的每 session 最多3份，失败也占名额；超时当 session 阻断，不能在运行间清理预算文件。采集节点跳过已下载正文，族锁沿用。

**怎么失败与降级**：单只股票查不到只记为该节点的逐目标失败，其余照常（节点"部分成功"）；整次扫描失败时会另发一条失败通知，且不会把"没有新申报"当成结论。休市日整个节点跳过。

[当天这一步](#stage-sec_monitor)

## ② 数据采集

**做什么**：按[适配器注册表](concepts/system.adapter_registry.md)的九族顺序打底：[日线](concepts/family.bars.md)、[资讯](concepts/family.news.md)、[SEC列表](concepts/family.sec_filings.md)、[财报日](concepts/family.earnings.md)、[FRED](concepts/family.fred.md)、手工维护的[纳指范围](concepts/family.universe.md)、[公司基本面](concepts/family.fundamentals.md)、[SEC正文](concepts/family.sec_text.md)、[行业归类](concepts/family.sector.md)。日线宽集合含ETF、池、真实/模拟持仓与挂单、活跃链、纳指范围、活跃研究请求；其它族按自身目标规则。研究或手动的显式单标的范围不扩大。ATR 已归[指标注册表](concepts/indicator.registry.md)，现算、不单独采集。

**读什么、写什么**：取数、存储、覆盖与补查共用适配器；数值变化追加 revision，不覆盖旧行，每族每 scope 记拉取结果。目标事实由宿主既有池/持仓算法传入，数据层不复制上层规则。指标包装保留两套 close/adj_close 均线、SPY/QQQ 相强差异，数值不改。

**怎么失败与降级**：沿用[外部请求策略](concepts/system.fetch_policy.md)，已有内部重试不再外套；备源单次尝试，attempts 合并真实次数。财报日实际启用 Nasdaq；日线正式只有 yfinance，Stooq 仅有解析/补缺机制，未启用。逐目标显示 source_used 与 fallback，取数成功但落库失败仍失败；某源没配置如实记，休市日跳过，见[备源](concepts/system.backup_source.md)。

[当天这一步](#stage-collect)

## ③ 筛选（screen-1）

**做什么**：在纳指 100 范围里按固定阈值挑出值得多看一眼的股票，让它们并入扩展观察池，之后规则照常运行。过滤条件是 20 日平均成交额达到 5000 万美元（见 [20 日平均成交额](concepts/indicator.dollar_volume_20.md)）、避开今天至其后第 5 个交易日内的已知财报（财报日未知的不排除）；触发了买入规则的股票排在前面，形态合格的再按[相对强弱](concepts/indicator.screen_rs.md)补齐，每天新入选不超过 20 只。成员有保留期和上限，细节见[每日筛选](concepts/system.screen.md)；它们属于扩展池，见[观察池分层](concepts/system.pool_tiers.md)。

**读什么、写什么**：读已采集的范围成分日线与财报日（这一步自己不再发请求），写筛选运行记录与结果；成员写入观察池分层；当天新入池、采集节点还没覆盖的成员，在这一步补采资讯与 SEC 列表。

**怎么失败与降级**：范围文件加载失败，或基准 QQQ 缺当日数据时，筛选记为出错，成员只保留已锁定和保留期内的、当天不新增，链继续。补采失败只让这一步"部分成功"。只对指定标的运行时整阶段跳过。

[当天这一步](#stage-screen)

## ④ 市场环境（regime-2）

**做什么**：给当天的市场打一个粗略的标签，并看行业强弱和利率背景，帮你判断一份买入计划是顺风还是逆风。四个部分：[广度](concepts/indicator.breadth.md)（范围与手动池里站上 MA50 和 MA200 的占比、新高新低数）；[区制](concepts/indicator.regime.md)（据广度判成进攻、中性、防守，样本不足 20 只时为样本不足）；[行业强弱](concepts/indicator.sector_strength.md)（SMH、XLK、QQQ 相对 SPY 的 20 与 60 日强弱）；利差背景，[HY 信用利差](concepts/indicator.hy_oas.md)与 [2s10s 利差](concepts/indicator.t10y2y.md)。

**读什么、写什么**：读日线和 FRED 序列，写一份当日市场环境快照。

**怎么失败与降级**：区制和利差**只给计划加提示，不改价位、仓位和有效期**。范围文件缺失时广度退回只用手动观察池。区制计算本身出错时，这一步重试一次，仍失败就停链：信号依赖当天的区制，不会在缺区制的情况下继续出计划，修好后可以从这里续跑。

[当天这一步](#stage-regime)

## ⑤ 信号与候选

**做什么**：规则 v1 在观察池和持仓上逐只评估，阈值事先固定、不用历史数据调参。下面是八条规则，每条的条件原文：

- [趋势回踩修复](concepts/rule.trend_reclaim.md)（买入）：收盘 > MA50 > MA200，且收盘重新站上 MA20，此前至少连续 3 天在 MA20 下方。
- [放量突破](concepts/rule.volume_breakout.md)（买入）：收盘创前 20 日收盘新高，成交量 ≥ 前 50 日均量 × 1.5，收盘 > MA50。
- [财报跳空向上](concepts/rule.earnings_gap_up.md)（买入）：8-K Item 2.02 后第一个交易日，开盘较前收盘跳空 ≥ +3%，收盘 ≥ 开盘。
- [跌破 MA50](concepts/rule.trend_break.md)（卖出）：此前至少连续 10 天收在 MA50 上方，当日跌破。
- [财报跳空向下](concepts/rule.earnings_gap_down.md)（卖出）：财报后第一个交易日跳空 ≤ −3%，收盘 ≤ 开盘。
- [持仓回撤](concepts/rule.position_drawdown.md)（卖出）：仅限没有计划的持仓，收盘较成本跌 ≥ 8%，或较持仓以来最高收盘回撤 ≥ 12%。
- [新财报](concepts/rule.new_filing.md)（观察）：当日出现新的 10-Q / 10-K / 8-K。
- [异常波动](concepts/rule.abnormal_move.md)（观察）：日收益绝对值 ≥ 近 20 日波动率 × 2.5，且当日无新财报。

另有两条只对**有计划的持仓**生效的卖出规则，它们同样产生候选：[计划止损](concepts/rule.plan_stop.md)（当日最低价触及当前止损）和[计划目标](concepts/rule.plan_target.md)（开仓以来首次触及 T1，卖出一半，只触发一次）。有计划的持仓用这两条，没有计划的持仓用持仓回撤。

财报时点按 SEC 受理时间判断：盘后发布算下一个交易日，盘前算当天，盘中发布不触发跳空规则。同一只股票当天的全部候选被合成一个[决策单元](concepts/system.decision_unit.md)；买入和卖出同日并存标记"冲突"、默认观察，没有持仓的卖出候选标记"回避"，见[冲突与回避](concepts/system.conflict.md)。

**读什么、写什么**：读日线、SEC 申报和你的持仓，写候选与决策单元，候选里冻结了当时的输入值。

**怎么失败与降级**：日线不足 200 根、缺当日日线或复权因子异常时，价格规则不产出候选，并记下跳过原因——这和"今天没有信号"是两回事，不会被写成无信号。

[当天这一步](#stage-signals)

## ⑥ 规则计划（plan-2）

**做什么**：只为"建议买入、无冲突、未决定"的单元生成规则计划。公式固定，价位四舍五入到 0.01：

- 参考价：信号日 T 的收盘价，见[参考价](concepts/plan.ref_price.md)。
- ATR：ATR14（Wilder 方法，拆股调整口径；系统只取最近 15 根日线，实际等于最近 14 个真实波幅的简单平均），见 [ATR14](concepts/indicator.atr14.md)。
- 入场区间：参考价 ± 0.5 ATR，见[入场区间](concepts/plan.entry_zone.md)。
- 止损：参考价 − 2 ATR，见[止损](concepts/plan.stop.md)。
- 目标 T1：参考价 + 4 ATR（2R），触及后卖出一半，见[目标 T1](concepts/plan.target1.md)；之后用[移动止损](concepts/plan.trailing_stop.md)。
- 有效期：T+1 至 T+3 个交易日，见[有效期](concepts/plan.validity.md)。
- 建议仓位：取 资金 × `risk_pct` ÷ 每股风险、单票上限、总持仓剩余空间三者中最小的；`risk_pct` 默认 1%，单票上限默认 25%，总持仓上限默认 100%（均为配置项）。缺资金快照时只给每股风险并标 `no_equity`，没有剩余空间则计划作废（`no_room`），见[建议仓位](concepts/plan.sizing.md)。
- 财报：有效期内有财报则计划作废；之后 10 个交易日内有财报，或日期未知、已过，则加提示，见[财报规则](concepts/plan.earnings.md)。
- 市场环境：冻结进计划的输入；防守区制、快照失败、行业弱势分别加提示，价位不变，见[市场环境提示](concepts/plan.context_flags.md)。

计划有[状态与作废原因](concepts/plan.status.md)。每份计划的价位都能只用它的输入所指向的已存行重算，批处理会做一次核对，结果是一致、不一致或不完整，见[推导与一致性](concepts/plan.derivation.md)；"推导一致"只说明算术正确。你采纳时也可以提交[自定计划](concepts/plan.own.md)。

**v2.9 起这一步生成 `plan-2`**：价位、有效期、仓位、财报规则与上面逐字相同，另写两个执行字段——入场类型恒为区间限价（见[入场类型](concepts/plan.entry_type.md)，开盘高开、低开怎么办见[开盘三种情形](concepts/plan.open_scenarios.md)）和最长持有 10 个交易日（见[最长持有](concepts/plan.max_hold.md)）。同一只股票已经有一条活跃的 Agent 计划链时，这一步照样生成规则基线，但会同时记下"这次触发并入了那条链"，不再为它另开一条链（见[一只股票一条活跃链](concepts/system.active_chain.md)）。旧的 `plan-1` 不重算。

**读什么、写什么**：读日线、财报日、区制和你的资金快照与敞口，写计划。同一单元重跑，结果相同就返回原计划，不同则记冲突、不覆盖。

**怎么失败与降级**：ATR 算不出（日线太少）则这份计划不生成并记原因；推导核对发现不一致或不完整时，这一阶段记为降级。

[当天这一步](#stage-plan)

## ⑦ Agent 建议

**做什么**：Agent 是"挑战者"：对每个买入候选独立给出一份计划，并行展示在规则计划旁。运行版本为 `agent-4`，计划 schema 仍为 `agent-3`。首版流程：

- **公司研究**：[研究员](concepts/agent.company_researcher.md)先读基本面、披露与截止前档案，不用价格工具、不写价位。facts/risks/blocked/summary 经引用校验后作为 `[fact:research]` 给提议者和反方，保留原 call 编号；失败或关闭只标原因，不阻断单元。
- **取数**：各阶段按版本化白名单使用工具；提议者/反方在既有十工具外增加 `get_indicator` 与 `request_data`。价格、申报等只读截止前入库数据，标的限制与调用参数/结果/哈希留痕继续保留，见[工具调用](concepts/agent.tool_calls.md)。补查工具只缓存请求，随运行落库，不能让本轮看到新数据，见[补查请求](concepts/system.data_request.md)。
- **`get_concept` 的例外**：它读的是概念卡（也就是你正在读的这类解释），随代码版本走，**不按截止时间回读**，结果标 `point_in_time: false`，记录里照样留痕。卡里只有定义，没有行情；见[概念卡](concepts/system.concept_card.md)。
- **提议**：给出买或不买，买时给入场区间、止损、T1，加 1 到 3 条[论点](concepts/agent.thesis.md)、1 到 5 条[失效条件](concepts/agent.invalidation.md)和 0 到 3 条[下次复查条件](concepts/agent.review_when.md)。每个价位和论点都必须[引用](concepts/agent.citation.md)本次真实发生的工具调用，价位还必须引用价格类工具；见[动作与价位](concepts/agent.action_levels.md)。
- **研究证据约束**：研究成功时首版至少一条论点引用研究阶段 call；研究非 ok 时豁免。修订/再触发/补查重跑沿用最近研究段，不重跑研究员，也不重复施加首版约束。
- **反方**：另一轮调用逐条对论点找[关键反证](concepts/agent.counter.md)，不能改价位和动作。
- **校验**：引用、价位顺序与 ±50% 区间、论点条数、条件合法性逐项检查，任一项不过整版作废，只显示原因，见[作废与跳过原因](concepts/agent.reject_reasons.md)。
- **第 0 版**：通过校验的建议成为[版本链](concepts/agent.version_chain.md)的第 0 版，之后每天复查。
- **解读**：卖出、观察、冲突的单元不给买卖建议，只写一份[解读](concepts/agent.brief.md)；失败时退回规则版摘要。

**读什么、写什么**：读上述工具能读到的数据，写 Agent 运行、全部工具调用、计划行、版本行和简报，同一事务里写入，任一步失败整体回滚。Agent 在全部规则计划写完之后才开始，失败只影响 Agent 轨道。

**研究入口与补查**：单元后先按 FIFO 处理[指定研究请求](concepts/agent.research_request.md)，再处理补查，共用剩余预算。daily 忙时研究提交仍成功返回200 queued；批外研究独立900秒、每日最多5次。研究只追加[档案](concepts/agent.research_dossier.md)，buy 结论也不写计划或开链。补采有新行才开新 cutoff 的一轮，每单元每天至多一次；剩余少于120秒则 deferred。运行明细分别给 research_requests 数字和 data_requests 五计数。

**怎么失败与降级**：每个单元最多尝试 3 次（用尽后可在运行详情里"重新运行 Agent"，重置记录只追加、不删旧运行）；从 Agent 节点开始计 1500 秒，之后不再启动新单元，前面采集的重试和等待不占这段时间；额度用尽或达到每日 token 上限时其余单元记为跳过；未连接 ChatGPT 或被关闭时整阶段跳过。价位来自模型，不是投资建议，也未经验证。

[当天这一步](#stage-agent)

## ⑧ 每日复查

**做什么**：对每条处于入场期或持仓期的 Agent 计划链（见[版本链状态](concepts/review.chain_state.md)）复查一次，结论是维持、修订或作废。复查先由系统自己看七项[系统信号](concepts/agent.signals.md)：新 8-K、异常波动、触及止损、触及 T1、临近到期、区制变化、新的用户输入；并判断最新版的失效条件和复查条件。然后按固定顺序得出[复查结论](concepts/agent.review_outcome.md)：

1. 任一失效条件成立：系统规则直接作废，不调用模型。
2. 没有信号、没有新用户输入、复查条件都没满足：系统规则直接维持，不调用模型。
3. 预算闸门（超出时限、Agent 不可用、额度或 token 上限）：写一条跳过记录。
4. 否则调用一次单阶段复查，让 Agent 判定维持、修订或作废。
5. 结论为修订时，再走一遍完整的提议、反方、校验；通过才成为[版本链](concepts/agent.version_chain.md)的新一版，并写明[相对上版变化](concepts/agent.changes.md)。

复查模型口径仍为 `agent-review-2`；修订运行使用 agent-4 并原样带入最近研究段，研究员不重跑。review 尾部也由同一 handler 处理补查，与 agent 共用预算；原运行截止时间不被修改。

**v2.9 起的三处变化**：① **一只股票一条活跃链**——入场期、持仓期、退出期的链都算活跃，同一只股票同一时间至多一条；它再次触发买入规则时，不开新链，而是作为[再次触发](concepts/agent.retrigger.md)并入已有的链：入场期的链跳过单阶段复查、直接产出修订版（价位不变也可以只顺延有效期）。② **持仓期的作废是退出指令**——止损触及、[时间止损](concepts/plan.max_hold.md)成立、失效条件成立或 Agent 判作废，都不再让链"结束"，而是进入退出期：不再复查，只每天写持仓状态，直到你把这些买入全部卖出。③ **每个持仓每天一行[持仓状态](concepts/review.holding_status.md)**——不论复查成功、跳过、出错还是 Agent 不可用，也不论有没有对应的链；这是"每天都有一行"的保证。

你贴入的研报、新闻和判断是[用户输入](concepts/agent.user_input.md)：它们只是证据，不是指令，会触发 `user_input` 信号，让当天的复查认真看一次。你也可以在「今日」点"重新复查"，立即对一条链复查一次；除非失效条件已经成立（那仍由系统规则作废），否则一定会调用模型。

**读什么、写什么**：读截止时间之前的数据和你的输入，写复查记录；修订时写新计划行与新版本行。每条链每个交易日至多一条有效的批处理复查，当天刚生成的版本不复查。

**怎么失败与降级**：修订没通过校验，或新版按规则作废时，只记一条没有新版的修订记录，最新版不变，该阶段记为降级；Agent 不可用时，系统能自己判定的照常写，其余写跳过。

[当天这一步](#stage-review)

## ⑨ 组合检查

**做什么**：组合功能开启时，先由宿主为真实账户和每个活跃模拟账户各算[组合硬检查](concepts/account.portfolio_check.md)，再为真实账户尝试一次[组合经理意见](concepts/agent.portfolio_manager.md)。硬结果不是 PM 意见：经理只能解释取舍和对 ok 计划建议排序，不能改任何价位或股数。

**读什么、写什么**：按本账户持仓、有效挂单、现金、权益和参数重算候选股数及行业敞口。只有入场期链头有 verdict/order/conflicts，持仓链不再次作为候选。行业上限默认0.35、同行业数量默认3；按链信号日与 ticker 贪心装入，装不下为 deferred。未归类单列 sector_unknown，仅提示、不受行业上限阻断。保存 portfolio-check-1 硬结果、cutoff 与参数；PM 成功才有 commentary。

**关闭功能**：`portfolio_manager=false` 时整个 portfolio 节点 `skipped/feature_disabled`，不运行硬检查；`[fact:portfolio_check]` 写「本版未检查」，`validate_orders` 收到 `None`，不作组合拦截；Today 为 `not_computed`，accounts 与原有现金、股数等订单校验照常。

**怎么失败与降级**：只有 `portfolio_manager=true`、硬结果已成功产出而后续 PM 模型失败时，才保留硬结果，显示 `degraded` /「只有硬结果」；真正缺行也是 `not_computed`，不显示空组合成功。PM 独立300秒、token 同计每日额度。有本账户硬结果时，模拟账户的 Agent 决策对 blocked 买单取首硬理由拒绝；照计划执行对照不接新 gate。硬检查仍是 Gate HOLD。

[当天这一步](#stage-portfolio)

## ⑩ 账户与模拟交易

**账本怎么记**：[账户账本](concepts/account.ledger.md)按本账户的初始现金、出入金与成交算现金，按先进先出与拆股算持仓，再以截止日或之前最近收盘估值。真实账户开账时已有持仓作为实物转入，不再扣现金；券商快照退为[对账](concepts/account.reconciliation.md)，有差额只提示，不改写账本。缺行情会注明；完整月报在未开账或缺月末权益端点时回退快照公式，并注明来源。

**模拟账户怎么行动**：[模拟账户](concepts/account.paper.md)从已有 Agent 计划中交易，分为[Agent 自管](concepts/account.agent_portfolio.md)与[照计划执行](concepts/account.follow_plans.md)。前者选择计划、股数、撤单与提前卖出，后者在上限内逐项按最大允许股数挂单；两者都不能改计划价位或取消自动退出。它们共用[模拟成交规则](concepts/account.fill_rules.md)：按日线结算，止损、T1 卖半与时间止损由宿主执行，默认费用 0、无滑点，结果不是真实成交。

**顺序与版本**：`stages-6` / `daily-3` 在组合检查节点后、推送前执行 accounts，即使组合功能关闭而节点跳过也照常：逐日补结算已有挂单与退出，写 account-1 权益与持仓状态，再作明日决策。成交仍 paper-fill-1，Agent 决策升为 agent-portfolio-2、增加组合检查事实段（关闭时为「本版未检查」），follow-plans-1 与 monthly-5 不变。旧决策保留 agent-portfolio-1 与原证据；账户页显示真实 agent_version，并标开关/版本/参数分界。Agent 不可用仍结算已有订单和自动退出，只跳过新决定。

**怎么比较**：[账户对比](concepts/account.comparison.md)使用同一截止时间与公式，曲线以各自开账当日或之后首个交易日的完整权益归一到 100；首日缺数据不改用更晚基准，并标明开账日、首个交易日、资金与缺数据。账户页本月收益计算上月末交易日至当前 session 的本月至今，完整月报仍按月末端点冻结。不排名，也不据此证明策略有效；周 / 月复盘与复盘卡仍只处理真实账户。

[当天这一步](#stage-accounts)

## ⑪ 复核与推送

**做什么**：组装今日视图，把摘要推送到 Discord。概览、新计划之后增加「🧩 组合检查」，再发计划更新（有更新时）、持仓、待处理与异常、筛选报告；组合消息区分硬判定与经理意见，无意见不补造理由。概览继续注明数据降级与复盘卡计数，见[每日推送](concepts/system.push.md)。推送范围仍是核心池、持仓与可决定的筛选单元，见[观察池分层](concepts/system.pool_tiers.md)。

**读什么、写什么**：读当天所有结果，写发送记录进运行报告。

**怎么失败与降级**：手动运行不推送；同一交易日已推送过就不重复；休市日只发一行"纽约休市，未运行"；推送失败只写进运行报告，下一次推送附"上次推送失败"；整条链停在某个节点时另发一条"每日工作流失败"通知。

[当天这一步](#stage-push)

## ⑫ 复盘

**做什么**：推送之后收尾，做两件事，哪件到期做哪件。其一，交易日检测最近 5 个交易日内结束的交易：持仓全部平掉，或你写过备注的未成交计划止损、到 T1、过期未成交、作废（有持仓的计划不因过期或作废而结束，只看平仓），见[结束事件](concepts/review.retro_end.md)。每个结束事件只建一张[复盘卡](concepts/review.retro_card.md)：程序把当时各版本的价位、之后的日线、三条轨道的 R、你的执行（成交关联了哪份计划、当时的备注）、持仓期的每日状态和纪律判定冻结成十一个事实（v2.9 之前的旧卡是十个，讲的是你的决定），Agent 只负责把这些事实讲成一段话并起草 0–3 条教训，数字它不能改。每次最多起草 5 张，起草不出来的卡之后再试，最多三次，起草有自己独立的 1500 秒时限。其二，任何日子都判断[周度复盘](concepts/review.weekly.md)和月度复盘是否到期：到期就生成冻结快照并推送（手动运行只生成不推送）。两件都没做时这一步记"今天没有到期的复盘"。

**读什么、写什么**：读成交、计划、备注、日线、持仓状态和复查记录（旧决定只读）；写复盘卡、起草记录、周 / 月复盘快照和复盘推送记录。教训草稿要你在卡页审定后才进入「我的知识」，审定之前任何 Agent 都读不到。

**怎么失败与降级**：这一步出错不会影响已经发出的计划推送。Agent 不可用、超出时限、额度或 token 上限时，卡照常建好，起草留到下一次；周 / 月复盘两部分各自独立，一个出错不拦另一个；某个标的读数出错只影响它自己，下次再试。

[当天这一步](#stage-retro)

## ⑬ 优化提案

**做什么**：仅北京时间周六的运行（纽约周五 session）在复盘后生成[优化提案](concepts/agent.optimize_proposal.md)，至多3条。宿主先冻结数据失败、Agent质量、重复教训、路线状态与未决提案五类证据；模型引用真实证据行才能写 proposed，不自动改代码、prompt 或策略。

**读什么、写什么**：读固定窗口事实与最新真实路线 revision，冻结证据包到该 optimize 运行 output.evidence_pack；页面 evidence_rows 从原包按 refs 取，不拿今日SQL重算旧证据。反方相关观测只是运行级后续成功修订比例（counter_followed_by_successful_revision），不证明逐条反方意见被采纳或因果关系。缺观测不伪造成零。

**批准边界**：[路线记录](concepts/system.roadmap_records.md)以系统内记录为准，历史只读真实 revision。批准前不得写版本或 brief；批准后才建 planned 版本与决定，并后台生成[实施 brief](concepts/agent.implementation_brief.md)。提交任务仅 pending，读回才知道 ok/error；brief 失败可重试，不撤销版本，更不自动实施。开关只停止新提案，不关闭这些阅读与决定入口。

**怎么失败与降级**：非提案日或开关关闭跳过，证据包全空则不调模型、记录 no_evidence；非法引用的提案丢弃，其余保留。独立600秒，token 同计每日上限。此节点在 push 与 retro 之后，提示进下一次周度复盘推送。所有优化判断仍为 Gate HOLD。

[当天这一步](#stage-propose)

## 你做什么：决定与成交

早上打开「今日」，只看计划表（[一只股票一条活跃链](concepts/system.active_chain.md)，每份计划写成[执行指令](concepts/plan.open_scenarios.md)）：入场期、持仓中、已结束三组，每行写明明早开盘落在区间内 / 高开 / 低开各做什么、本计划何时作废。**没有任何按钮**：你自己在券商下单，然后到「账户 → 概览」的真实账户成交表单录入成交，并关联当时照着做的那份计划（默认选中活跃链的最新版，也可以选规则基线、自定价位或「无计划」），见[持仓与成交](concepts/review.position.md)。v2.9 之前的采纳 / 否决 / 降级[决定](concepts/system.decision.md)已退役，旧决定只读保留。

有计划的持仓会按[计划止损](concepts/rule.plan_stop.md)和[计划目标](concepts/rule.plan_target.md)每天判断：触及 T1 之后，止损变为 开仓以来最高收盘 − 3 ATR 与持仓成本中较高者，收盘后更新、次日生效，见[移动止损](concepts/plan.trailing_stop.md)。如果你自己调整价位，会另存一份[自定计划](concepts/plan.own.md)。

## 从计划到成交

系统给的是一份份「可以照着做」的计划；你做没做、照哪一份做，由你的成交说话。

- **关联计划**：录入成交时选当时照着做的那一版；只能关联成交那一刻的链头——补录昨天的成交，关联的仍是当时看到的那一版（比它旧的版本被拒 `plan_stale`，成交之后才生成的版本被拒 `plan_after_fill`），退出期的链不再提供。选「自定」会在同一个事务里写一份[自定计划](concepts/plan.own.md)；不选就是「无计划」。
- **执行状态**：由成交推出，不用点按钮——[已采纳 / 有效期外成交 / 等待成交 / 未执行，没有关联计划的买入是计划外](concepts/review.execution_status.md)。链上有任一关联买入就是「已执行」，同时写明照什么执行（Agent 第 n 版、规则基线或自定）。
- **为什么没有决定按钮**：交易日志本身就是采纳的证据，再点一次按钮只会多出两份可能互相矛盾的记录；否决和降级也没有对应的行为——不买就是未执行。旧决定表保留只读，让旧复盘卡和旧周报照旧可读。
- **备注**：想给自己留句话，可以给一条链或一份计划写备注（接口支持更正与撤回，页面暂未提供）。备注只给你自己看，不进任何 Agent 提示词、不触发复查；想让 Agent 参考，请用「我的补充」。
- **持仓之后**：每个持仓每天有一行[持仓状态](concepts/review.holding_status.md)（距止损、距 T1、持有天数、指令和维持理由），到最长持有仍没到 1R 会给出退出指令；这些指令进入纪律的「死扛亏损」判定，也进入复盘卡的「持仓」事实。

## 复盘口径

复盘不评价你"对不对"，只把"计划怎么说、系统怎么算、你怎么做"并排放好：

- **计划影子**：按计划的价位逐日独立模拟，结果用 [R](concepts/review.r_multiple.md) 计，详见[计划影子](concepts/review.plan_shadow.md)；
- **对照组**：全部单元（v2.9 起不再只看已决定的）假设下一交易日开盘入场，看 5、10、20 个交易日的超额，见[对照组](concepts/review.control_group.md)；
- **纪律四项**：[追高进场](concepts/review.discipline_chase.md)、[过早止盈](concepts/review.discipline_early_take.md)、[死扛亏损](concepts/review.discipline_held_loser.md)、[计划外交易](concepts/review.discipline_unplanned.md)；
- **三方对比**：规则、Agent、实际三条轨道并列，见[三方对比](concepts/review.tracks.md)与[状态含义](concepts/review.track_status.md)；Agent 轨道取你照着做时看到的那一版（成交关联的版本，旧决定则取它记下的版本，见[依据](concepts/review.version_basis.md)），不拿事后修订美化；
- **首版与最新版**：每条 Agent 链分别只算首版和只算最新版，避免样本被修订次数放大，见[首版与最新版汇总](concepts/review.first_latest.md)；
- **周度与月度复盘**：[周度复盘](concepts/review.weekly.md)冻结当期快照，其中"[你学到了什么](concepts/review.weekly_learning.md)"汇总本周的复盘卡和被检验的知识；[月度收益与分档](concepts/review.monthly_return.md)、[流程复查触发](concepts/review.process_check.md)和[风险敏感性表](concepts/review.sensitivity.md)看账户层面；
- **复盘卡与我的知识**：每笔结束的交易有一张[复盘卡](concepts/review.retro_card.md)，你审定的教训进入[我的知识](concepts/system.my_knowledge.md)，被引用后可以按计划影子回看[表现](concepts/review.knowledge_performance.md)；
- **上线前检查**：新规则上线前用库内已有数据回看，见[上线前检查](concepts/review.backcheck.md)。

这些都是描述性统计：样本小、信号同日重叠，只给计数、均值、中位数和分布，不宣称任何规则或 Agent 有效。

## 怎么在系统里核对

不用读仓库文档，下面几个页面各回答一类问题：

- **今天这次运行跑了什么**：「今日」顶部的运行一览，十三节点各一句话，附计数、状态和跳过原因，可以选日期看往日，旧运行按原版本展示。状态含义见[阶段状态](concepts/system.stage_status.md)，节点定义见[每日节点](concepts/system.stages.md)。
- **哪个数据源对哪个目标没拿到、为什么、卡在哪个节点**：「工具 → 运行记录」点进一次运行的详情页：节点状态图、每个节点的"做了什么 / 拉取详情 / 结果详情"；失败的节点上有"从此节点继续"，续跑后已完成的节点只有第 1 次记录（见[每日工作流](concepts/system.daily_workflow.md)）。
- **一份计划的价位怎么来的**：计划卡上的"展开推导"，顺着触发条件、输入快照、逐项计算和校验看一遍，并告诉你推导是否一致。
- **一份 Agent 计划的每个字段和数字从哪来**：推理阅读先看公司研究，再看提议者、反方与复查；原 call 编号不变，缺研究段注明原因。研究页另看请求档案，不把研究结论当自动开链。
- **采集到了什么数据**：「数据」菜单，看九族覆盖、逐目标来源/失败与原始值；指标组列22项真实窗口与口径，不另造 ATR 数据族。
- **硬约束与经理建议分别是什么**：今日/账户按真实 result/commentary 展示；组合功能关闭或缺行是 not_computed，开启且已成功产出硬结果、后续 PM 失败才是 degraded，完整为 ready。
- **路线为何改变**：Roadmap 看真实修订、生成时冻结证据、人工决定与 brief 状态；设置页看四开关的全部真实变更历史，当前值不能冒充历史。
- **某个词是什么意思**：「知识」菜单的"概念"，或者在各页面上点带虚线下划线的概念名，就地展开摘要，再一跳到完整的概念卡。
- **某只股票今天为什么值得关注**：「标的」页，首屏列出信号与计划，K 线上标出候选、各版本计划价位和你的成交。
