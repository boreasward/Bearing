# 02 — Phase 1：数据地基（精简版）

> **已归档（2026-10-23）**：本文属于 roadmap v1，已被 [roadmap v2](../v2/roadmap/roadmap.md) 取代，仅作历史参考，不再更新。

> 目标：只补**被 Phase 0 事件研究证明有用**的数据，不平铺采集。三件事：OHLCV 落库（回测/事件研究的可复现基底）、市场广度指标（替代大部分宏观）、EPS surprise 结构化（喂 PEAD 事件研究）。
> 依赖：Phase 0 的决策门结论；现有 `data/price.py`、`filings/sec_downloader.py`、`config.py` 已预留的 `PriceConfig`/`MacroConfig`/`FundamentalsConfig`。
> 历史估算：1.5–2 周，不是本轮新排期。

## 2026-10-17 状态与本轮最小补强

通用 OHLCV 读库优先、市场广度、FRED 和 EPS surprise 均未完成。10 月 A 股/美股研究已有专用行情、SEC Company Facts 财务、来源及指引能力，但公司指引不是市场一致预期，研究表也不是通用数据地基。历史交付见 [跨市场报告](../v2/features/fundamentals.md)，用户验收仍 pending。

[Phase 0 评审](reports/2026-10-17-phase0-review.md)全部正式 Gate HOLD。它支持先解决“旧信号日期/计算口径不可追溯”的最小工程缺口，而不支持扩池、补采、接入 FRED/一致预期或开始全组合回测。本轮第二轮修复正在新增 `015_signal_provenance.sql`：快照加 `price_date` / `calculation_version`，评分加 `calculation_version`；老行 null、不回填，隔离不可信输入，冲突保留旧记录。仅新增迁移文件，未执行到真实 DB，最终复核与全量测试待完成。这不是整个 Phase 1 的交付。

下文保留长期方案。所有旧预占编号取消，未来迁移实施时再分配；实际 009–014 用途见 [编号表](README.md#4-迁移编号约定)。启动长期项前，先补最终离线结果、可连接环境只读 schema/覆盖核验、可信历史输入/区制和独立样本设计，再确认范围及授权。

---

## 取舍原则

第一版 roadmap 想「FRED 宏观全采 + 全 XBRL 财务抽取 + 多源价格」。**精简掉**：

- ❌ 6 个 FRED series 全采 → ✅ 只要 **HY 信用利差 + 2s10s**（其余滞后指标推迟，见 [00 §3.3](00-principles-and-direction.md)）
- ❌ 全 XBRL companyfacts 大工程 → ✅ 只抽 **EPS 实际/预期、营收、指引** 三项（喂事件研究够用）
- ❌ 多付费数据源（Tiingo/Polygon） → ✅ 先 yfinance + 落库 + 重试，单源够用，质量靠 Phase 0 的复权校验兜底
- ✅ **市场广度** = 新增重点（旧 roadmap 没有，性价比最高）

---

## Task 1.1 — 通用 OHLCV 落库 + 可靠性（规划，编号待定）

**缺口**：研究支线已有专用行情归档/存储，但通用扫描与事件取价尚未统一为增量、读库优先的可复现价格层。此任务保留为长期方案，不由本轮最小补强扩展实施。

**未来 `ohlcv_bars` 迁移草案（编号待定）**：

```sql
CREATE TABLE IF NOT EXISTS ohlcv_bars (
  ticker      TEXT NOT NULL,
  bar_date    DATE NOT NULL,
  open        NUMERIC, high NUMERIC, low NUMERIC, close NUMERIC,
  adj_close   NUMERIC,            -- 复权收盘，事件研究/回测用这列
  volume      BIGINT,
  source      TEXT NOT NULL DEFAULT 'yfinance',
  fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (ticker, bar_date)
);
CREATE INDEX IF NOT EXISTS idx_ohlcv_ticker_date ON ohlcv_bars (ticker, bar_date DESC);
```

**做什么**：
- `data/price.py` 取价层加 **重试 + 指数退避**（复用 `PriceConfig.max_retries/backoff_seconds`，已在 config 预留）。
- 落库为「增量 upsert」：只补缺失的 bar，已有不重拉。
- `get_price_history` / `get_price_history_asof` 优先读库，缺口才回源。
- **新鲜度守卫**：按实际 bar 日期验证而不是把旧收盘价标成今天；本轮已修技术扫描目标日/默认完成日与历史评分同日输入，通用取价层的可靠性仍需独立验收。

**测试**：upsert 幂等、缺口检测只拉缺失段、退避在连续失败时被调用（mock）、`signal_date` 取最新 bar 日。

---

## Task 1.2 — 市场广度指标（规划，编号待定）

**为什么**：对股票交易者，广度比滞后宏观更灵敏地反映「市场是否健康」。用已落库的 OHLCV 就能算，几乎零额外数据成本。

**指标**（对一个标的全集 / 或一个代表性宽基成分池）：
- `pct_above_200ma`：成分股中站上 200 日线的百分比
- `pct_above_50ma`
- `new_highs_minus_lows`：52 周新高 - 新低
- `advance_decline_line`：涨跌家数累积线（A/D Line）

**未来 `market_breadth` 迁移草案（编号待定）**：

```sql
CREATE TABLE IF NOT EXISTS market_breadth (
  breadth_date         DATE PRIMARY KEY,
  pct_above_200ma      NUMERIC,
  pct_above_50ma       NUMERIC,
  new_highs_minus_lows INT,
  advance_decline      INT,
  universe_size        INT,         -- 计算基数，用于解释百分比
  computed_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**做什么**：
- `market/breadth.py`：从 `ohlcv_bars` 计算上述指标。成分池可先用 watchlist + 主要指数 ETF 的成分近似，或退而用一个固定的代表性篮子。
- 接入市场状态分类：把 green/yellow/red 从「纯 VIX 阈值」升级为「VIX + 广度 + HY利差」多因子（见 Task 1.3）。

**测试**：构造 mock 多股价格，断言 `pct_above_200ma` 计算正确；断言空成分池时优雅降级。

---

## Task 1.3 — 精简宏观：HY 利差 + 2s10s（规划，编号待定）

**为什么砍**：宏观择时战绩差、CPI/就业滞后（见 [00 §3.3](00-principles-and-direction.md)）。只保留两个有 risk-off 价值的。

**做什么**：
- `data/fred.py`：只拉 `BAMLH0A0HYM2`（HY OAS）和 `DGS10`-`DGS2`（2s10s 利差）。FRED API 免费需 key（`FRED_API_KEY` env）。
- 落入一张小表（可并入 `market_breadth` 或单建 `macro_indicators`，字段少，建议单建以备扩展）。
- `MacroConfig.fred_series` 从 6 个**精简为 2 个**（改 config 默认）。

**测试**：mock FRED 响应解析、缺 key 时降级跳过不崩。

**市场状态多因子门控（Task 1.2 + 1.3 汇合）**：

```
red    : VIX>30  OR  pct_above_200ma<20%  OR  HY利差快速走阔
yellow : VIX>20  OR  SPY<200MA            OR  pct_above_200ma<40%
green  : 其余
```

> 阈值先用经验值，**不在此处寻优**（避免过拟合）；待 Phase 0/3 事件研究验证哪个门控因子真正有区分度后再调。

---

## Task 1.4 — EPS surprise 结构化（规划，编号待定）

**现状与缺口**：10 月研究支线已能读取 SEC Company Facts、解析部分原文并保留来源，不再是“只下载 HTML”。但可信历史 EPS 实际/市场一致预期、公布时点和 surprise 事件源仍未实现；公司指引不能当一致预期，不能用占位数据跑 PEAD Gate。

**精简范围**：不做全 XBRL companyfacts，只要三项：
- EPS 实际 / 预期 / surprise%
- 营收 实际 / 预期
- 指引变化（有/无、上调/下调，能从 8-K 文本简单抽取则抽，否则先留空）

**未来 `fundamentals` 迁移草案（编号待定）**：

```sql
CREATE TABLE IF NOT EXISTS fundamentals (
  ticker          TEXT NOT NULL,
  period_end      DATE NOT NULL,        -- 财报对应季度末
  report_date     DATE NOT NULL,        -- 实际公布日（事件研究锚点）
  eps_actual      NUMERIC,
  eps_estimate    NUMERIC,
  eps_surprise    NUMERIC,              -- (actual-estimate)/|estimate|
  revenue_actual  NUMERIC,
  revenue_estimate NUMERIC,
  source          TEXT NOT NULL DEFAULT 'yfinance',
  fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (ticker, period_end)
);
CREATE INDEX IF NOT EXISTS idx_fundamentals_report ON fundamentals (ticker, report_date DESC);
```

**数据源策略**：
- 一期：yfinance `earnings_dates` / `get_earnings` 拿 EPS 实际/预期（够 PEAD 用，免费）。
- 二期（可选）：SEC XBRL `companyconcept` 补营收/毛利（`FundamentalsConfig.concepts` 已预留）。
- 把 `report_date` 作为事件研究的锚点（公布日，不是季度末）。

**测试**：surprise% 计算（含 estimate=0 的除零保护）、report_date 锚点正确、缺数据降级。

---

## Phase 1 完成标志

- `ohlcv_bars` 有数据，价格读库优先、可复现，`signal_date` 新鲜度修复。
- `market_breadth` 每日计算，市场状态升级为多因子门控。
- HY 利差 + 2s10s 入库（FRED key 可选，缺则降级）。
- `fundamentals` 有可信 EPS surprise 和公布时点，Phase 0 的 `eps_beat/miss` 从不可用变为可验证的事件源，才可运行 PEAD 研究。

> 衔接：数据就绪仅允许开始重评，不保证得到可信的正向 PEAD 结论；仍需处理历史可见性、样本依赖、选择偏差和成本，不能直接作为交易计划依据。
