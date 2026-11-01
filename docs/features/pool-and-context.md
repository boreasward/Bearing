# 观察池与市场环境

观察池决定批处理跑哪些股票、推送哪些；市场环境是每份计划都附带的背景，**只作提示，不改价位**。上线前检查（`backcheck-2`）用库里已有的数据，检查规则是否触发过频、止损是否总被扫掉。

设计：[P2 观察池扩展与市场环境](../specs/2026-10-23-p2-pool-and-market-context-design.md)。

## 观察池与分层

| 层 | 规模 | 规则、计划、Agent | 推送与「今日」 |
| --- | --- | --- | --- |
| 核心池（`core` 标签） | ≤ 15 只 | 运行 | 推送；进入待决定列表 |
| 扩展池 | 其余，总数 ≤ 100 只 | 运行，影子跟踪（仓位照算） | 不推送；只在「今日」底部的折叠区只读显示，进入周度复盘 |
| 持仓股 | 不论在哪一层 | 运行，按核心池对待 | 推送 |

- 观察池 = 数据库观察名单中 `active` 且是有效美股代码的行。代码无效的行（例如迁移前遗留的旧数据）会被跳过，在列表里标出「证券身份无效」，可以删除。
- `sector:<ETF>` 标签把个股映射到行业 ETF（SMH / XLK / QQQ）。
- 每次批处理把分层变化追加进 `pool_tiers`（`effective_session` 为本次交易日）。某只股票某一天属于哪一层，按「`effective_session <= 该日` 且 `recorded_at <= 截止时间`」的最新一行还原。
- 超过上限时，写入观察名单和批处理都会报错，不会悄悄截断。

## 每日筛选 `screen-1`

每个交易日收盘后（嵌在 `workbench` 批处理里，ETF 采集之后、逐股采集之前）对纳指 100 跑一次：

- **范围**：`config/universes/nasdaq100.json`（版本化文件、手动维护，`GOOGL` 留、`GOOG` 去）。已在手动观察名单里的股票不参加，它们本来就每天在跑。`as_of` 距批处理日超过 120 天记 `universe_stale`，照常运行。
- **采集**：对范围内、不在观察池里的股票只采日线和财报日（复用现有增量采集与修订规则）；首次运行回补约 300 根日线。入选进池之后，按观察池流程补采资讯和 SEC 列表。
- **过滤与排序**（`screen-1`，阈值固定不调参）：数据前置不通过的记跳过原因（不算过滤）；20 日平均成交额 ≥ 5000 万美元；财报排除窗口 5 个交易日；触发组（当日 `trend_reclaim` / `volume_breakout` 触发买入且无卖出候选）优先，形态组（收盘 > MA50 > MA200 且 ATR14/收盘 ∈ [2%, 6%]）按相对强弱 RS = ½(r20−r20(QQQ)) + ½(r60−r60(QQQ)) 补齐，每天新入选 ≤ 20 只。
- **成员与轮换**：当天成员 = 今天入选 ∪ 保留期内（最近入选 ≤ 10 个交易日，含当天）∪ 锁定（有效计划或持仓），减去手动观察名单。锁定只作用于来自筛选的股票（当前是筛选成员，或曾是筛选成员、后来转入手动名单又删除），不在池里的持仓和从未入选的股票不会因为锁定变成筛选成员（持仓照常单独参加批处理）；上限 `min(40, 100 − 手动观察池数)`，超出按此优先级保留，挤出的记 `summary.screen.evicted`；锁定数超上限时全部保留并记 `screen_locked_over_cap`，当天不新增。范围内的规则只用来判断触发组，不写 `signal_candidates`；进池之后规则 v1 照常运行。
- **分层**：筛选成员一律 `tier = extended`、`source = screen`，不占核心池名额；`tier_changes` 在 `tier`、`sector_etf`、`source` 任一变化时追加一行。手动加入观察名单的股票 `source` 翻回 `watchlist`；从名单删除后若仍在保留期，翻回 `screen`。
- **出错**：范围文件加载失败或 QQQ 缺当日数据时 `screen.status = error`，成员只保留锁定和保留期内的（不新增），批处理继续；QQQ 缺数据时范围文件已加载，区制广度仍用范围 ∪ 手动观察池；单只采集失败记 `summary.sources.bars` 与跳过原因；筛选步骤超过 600 秒记 `screen_over_budget`。结果与报告写进 `screen_runs` / `screen_results`（同一交易日重跑追加新行、读取取最新）。

## 市场环境 `regime-2`

每次批处理在采集之后、规则之前，生成一份快照写入 `market_regimes`：

- **广度样本**：纳指 100 范围 ∪ 手动观察池中当日有 K 线的股票（池会每天变，单用池内广度会跟着名单波动），按 `adj_close` 计算站上 MA50 / MA200 的占比，以及 52 周新高、新低数量。持仓股如果不在样本里，不计入广度。范围文件加载失败时退回只用手动观察池，记 `regime_universe_missing`。旧快照保留 `regime-1` 口径。
- **区制标签**：MA200 合格样本 < 20 只时为「样本不足」；站上 MA200 占比 ≥ 60% 且站上 MA50 占比 ≥ 50% 为「进攻」；分别 < 40% 且 < 30% 为「防守」；其余为「中性」。
- **行业强弱**：SMH、XLK、QQQ 相对 SPY 的 20 日 / 60 日强弱，分为弱、强、分化、数据不足。
- **利差背景**（FRED）：`BAMLH0A0HYM2`（HY OAS）和 `T10Y2Y`，各取 120 天，附 20 个观测前的变化；数据修订时追加记录。
- **对计划的影响**：分层、区制、行业和下次财报日冻结进计划的 `inputs.context`；防守区制、快照失败、行业弱势分别加提示 `regime_risk_off` / `regime_unavailable` / `sector_weak`，价位、仓位和有效性都不变。

## 上线前检查 `backcheck-2`

新规则上线前手动运行。只读库里已有的 K 线（当前修订）和 SEC 文件，不联网、不补采：

- 每只股票的前 200 根 K 线只用来预热指标；评估区内的每个交易日，只用截至当日的 K 线跑规则（假设无持仓）。买入候选按 `plan-1` 算价位，再用 `plan-shadow-1` 模拟结果。
- 按规则逐条统计，再给合计：触发数、每 100 个股票日的触发数、同一天 ≥ 3 只股票同时触发的天数、成交数与过期数、5 个交易日内止损的占比、到达 T1 的占比、R 的均值 / 中位数 / 四分位。
- 信号后超额（`backcheck-2` 新增）：每条信号、每个 h ∈ {5, 10, 20} 个交易日，`close(标的, t+h) / close(标的, t) − close(SPY, t+h) / close(SPY, t)`，按收盘价对收盘价。第 h 日在最后一根日线之后计 `immature`；个股或 SPY 缺任一端点计 `missing`，都不进统计、不用邻近值替代。每行给出 n、均值、中位数、正超额占比、immature 与 missing 计数。超额不代表可成交。
- 标记：`too_frequent`（每 100 个股票日触发 > 5 次）、`stops_swept`（成交 ≥ 10 且 5 日内止损 ≥ 50%）、`insufficient`（成交 < 10）。
- 结果冻结进 `rule_backchecks`（已冻结的 `backcheck-1` 旧记录保留，前端超额列显示「—」）。这只是描述性检查，不调参，也不代表规则有效。

## 入口与验证

- 页面：「设置 → 关注列表」（核心池勾选、行业 ETF 下拉，「下一事件」列显示今天及以后最近的财报日，表头显示「美股池 n/100 · 核心 m/15 · 筛选 j/40」；超限或标签不规范时返回 400，观察名单不变）；「今日」顶部的市场环境与筛选卡片（形态候选默认前 10 只，「展开全部」拉取完整报告），待决定的筛选单元带「筛选」徽标；「复盘 → 上线前检查」。
- API：`GET/POST /api/watchlist`、`DELETE /api/watchlist/{ticker}`、`GET /api/watchlist/resolve?symbol=`；`GET/POST /api/workbench/backchecks`（body 可以带 `tickers`）；`GET /api/workbench/screen?session=&full=1`（缺省最近一次，`full=1` 附全部结果行；无记录返回 `report: null`）；`POST /api/workbench/screen/replay`（body `{"sessions": N}`，1 ≤ N ≤ 20 缺省 5，越界 400）。
- 命令行：`python -m investment_assistant.optimize.backcheck [--tickers A,B]`；`python -m investment_assistant.analysis.screening replay [--sessions N]`（打印 JSON）。
- **回放（只读）**：对最近 N 个已收盘交易日逐日重跑 `screen-1`，每日只用当日及以前的 K 线，不写任何表、不推送；已知局限：财报日用当前已知值而非当时时点值，报告里逐份标注。
- 运行报告：`summary.pool`（`core` / `extended` / `holdings` / `screen` / `changes`）、`summary.screen`（`status`、`selected`、`counts`、`skips`、`evicted`、`removed`、`flags`、`timing`）、`summary.regime`、`summary.etfs`、`summary.macro`（未配置 FRED 时记 `not_configured`）、`summary.timings`（v2.8 起没有整批预算，不再记 `batch_over_budget`）。
- 验证一次：给某只股票勾选「核心」，下一次批处理的 `pool.changes` 里应出现一条 `tier: core`；运行报告里有 `regime: {status: inserted, label: …}`，配置了 FRED 时还有 `macro.BAMLH0A0HYM2.inserted > 0`。

## 已知限制

- 观察池里有 200 根 K 线的股票不足 20 只时，区制一直显示「样本不足」。
- 当前观察池历史成交太少，上线前检查通常标为 `insufficient`，这是正常现象。
