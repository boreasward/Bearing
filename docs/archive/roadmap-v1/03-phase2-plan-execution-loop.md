# 03 — Phase 2：计划-执行-复盘闭环 ★系统价值核心

> **已归档（2026-10-23）**：本文属于 roadmap v1，已被 [roadmap v2](../v2/roadmap/roadmap.md) 取代，仅作历史参考，不再更新。

> 目标：把你目标②（交易计划）和目标③（复盘）接成**同一个闭环的两端**——`execution_plans` 与 `trade_journal` **共享主键**，从而量化「计划遵守度」和「实盘期望值」。
> 这是整个系统的护城河所在（见 [00 §4](00-principles-and-direction.md)）：用你自己的真实交易数据做归因，无幸存者偏差、样本就是你关心的全部。
> 依赖：Phase 0 的事件研究（计划的依据）、Phase 1 的价格/财报数据（计算实盘收益）。
> 历史估算：2 周，不是本轮新排期。

**2026-10-17：本阶段未落地。** 以下 schema、API 与计算模块是长期设计，不是已有能力。研究观察卡/公司指引不等于计划和成交日志；当前六入口 UI 的“交易指令”也不构成闭环。013 已用于 A 股研究、014 已用于美股研究，plans/journal 的迁移编号需在未来实施时另分配。Phase 0 正式 Gate 全部 HOLD，尚不能把探索性均值写入可信的 `expected_edge`；本轮不录入真实交易或执行交易。

---

## 核心设计：共享主键的闭环

```
                    ┌──────────────────┐
   信号+事件研究 ──▶ │ execution_plans  │  我「打算」怎么做
                    │ (计划: 进/止/目/仓)│
                    └────────┬─────────┘
                             │ plan_id (共享主键 / 外键)
                    ┌────────▼─────────┐
   实际成交 ──────▶ │  trade_journal   │  我「实际」怎么做
                    │ (执行: 价/量/时/由)│
                    └────────┬─────────┘
                             │ 收盘价 / 事件研究基线
                    ┌────────▼─────────┐
   Phase 3 复盘 ◀── │   归因 / 对比      │  「计划 vs 执行 vs 理论」差在哪
                    └──────────────────┘
```

**关键**：没有这个共享主键，计划和日志就是两张孤立的表，复盘只能做单笔叙事。有了它，「我有没有听自己的话」「听话到底赚不赚钱」变成可计算的量。

---

## Task 2.1 — 交易计划落表（规划，编号待定）

**现状**：`research/execution_plan.py` 的 `create_execution_plan(ticker, direction, premise)` 是死代码，无调用方，且不含量化字段。

**做什么**：扩展为含可执行量化字段，并真正接入决策流。

**未来 `execution_plans` 迁移草案（编号待定）**：

```sql
CREATE TABLE IF NOT EXISTS execution_plans (
  plan_id        BIGSERIAL PRIMARY KEY,
  ticker         TEXT NOT NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  direction      TEXT NOT NULL,           -- long / short / watch / avoid
  conviction     SMALLINT,                -- 0-100 信心
  premise        TEXT,                    -- 论点（信号 + 事件研究依据）
  entry_price    NUMERIC,                 -- 计划进场
  stop_price     NUMERIC,                 -- 计划止损
  target_price   NUMERIC,                 -- 计划目标
  r_multiple     NUMERIC,                 -- (target-entry)/(entry-stop)，自动算
  position_pct   NUMERIC,                 -- 计划仓位 %
  regime         TEXT,                    -- 当时市场状态 green/yellow/red（连接目标①）
  status         TEXT NOT NULL DEFAULT 'open',  -- open / executed / expired / cancelled
  source_signal  TEXT,                    -- 触发信号类型，回连事件研究
  expected_edge  JSONB                    -- 事件研究给的前瞻收益基线快照
);
```

**信号 → 计划的映射逻辑** `guidance/plan_builder.py`：

```
(市场状态, 策略分, 触发信号, 事件研究前瞻分布) →
  {direction, conviction, entry, stop, target, r_multiple, position_pct}
```

- **止损 / 目标**用 ATR 锚定（如 entry - 1.5×ATR 为止损），而非拍脑袋。
- **仓位**用波动率反比 + conviction 调整（高波动小仓）。
- **`expected_edge`** 直接快照 Phase 0 事件研究对该信号的前瞻收益分布——让计划「有据可查」，事后能回连验证。
- **regime** 用 `compute_market_signal_for_date` 打标签。

**测试**：r_multiple 计算、ATR 止损锚定、avoid 状态下不生成多头计划、expected_edge 快照写入。

---

## Task 2.2 — 交易日志落表（规划，编号待定）

**设计要点**：日志不只是流水，要能**推导组合状态**（见 [05](05-cross-cutting.md) 组合敞口），并能**回连计划**。

**未来 `trade_journal` 迁移草案（编号待定）**：

```sql
CREATE TABLE IF NOT EXISTS trade_journal (
  trade_id     BIGSERIAL PRIMARY KEY,
  plan_id      BIGINT REFERENCES execution_plans(plan_id),  -- 可空：临时起意的交易无计划
  ticker       TEXT NOT NULL,
  side         TEXT NOT NULL,           -- buy / sell / short / cover
  exec_date    TIMESTAMPTZ NOT NULL,
  exec_price   NUMERIC NOT NULL,
  quantity     NUMERIC NOT NULL,
  fees         NUMERIC DEFAULT 0,
  reason       TEXT,                    -- 为什么这么做（自由文本，喂 LLM 复盘）
  emotion      TEXT,                    -- 情绪标签：fomo/confident/fearful/disciplined...
  regime       TEXT,                    -- 当时市场状态
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_journal_ticker ON trade_journal (ticker, exec_date);
CREATE INDEX IF NOT EXISTS idx_journal_plan ON trade_journal (plan_id);
```

**录入方式（2026-07-04 已确认）**：前端表单从零录入。券商 CSV 和 Obsidian 解析按 YAGNI 不纳入当前设计；将来有实际需求再另行确认。这里不再列为“待你确认”。

**仓位 / 持仓重建** `services/portfolio.py`：
- 从 `trade_journal` 按 ticker 聚合 → 当前持仓（数量、均价、未实现盈亏）。
- 按 side 配对开平仓 → 已实现交易（用于期望值统计）。
- FIFO / 移动加权均价二选一（建议移动加权，简单且够用）。

**测试**：开平仓配对、均价计算、部分平仓、做空路径、未实现盈亏用最新 `ohlcv_bars` 收盘价。

---

## Task 2.3 — 计划遵守度分析

**这是闭环的第一个产出**，回答「我有没有听自己的话」。

`services/adherence.py`：对每个 `executed` 的计划，比对实际成交：

| 维度 | 计算 | 含义 |
|------|------|------|
| 进场偏离 | `(exec_price - entry_price)/entry_price` | 追高了还是等到了 |
| 止损遵守 | 实际是否在 stop 附近离场，还是死扛穿损 | 纪律 |
| 目标遵守 | 是否到目标才走，还是提前跑 | 截断利润? |
| 仓位偏离 | 实际仓位 vs 计划仓位 | 过度自信/胆怯 |
| 持有周期 | 实际 vs 计划隐含 | |

**输出**：每笔交易的遵守度评分 + 聚合统计。

**测试**：各偏离维度计算、无计划交易（plan_id 空）跳过、穿损识别。

---

## Task 2.4 — 实盘期望值统计

**回答「我的交易整体是正期望吗」**，是 Phase 3 三方对比的「你」这一方。

`services/trade_stats.py`：从配对后的已实现交易算：
- 胜率、平均盈 / 平均亏、盈亏比、期望值 `E = 胜率×均盈 - 败率×均亏`
- 最大回撤、Sharpe（按交易序列或日度权益曲线）
- **按维度切片**：按 regime / 按信号类型 / 按是否有计划 / 按情绪标签

**关键切片**——「遵守计划的交易 vs 临时起意的交易，期望值差多少」：这一条直接量化「听自己话是否赚钱」，是 [00 §3.2](00-principles-and-direction.md) 闭环价值的兑现。

**测试**：期望值公式、回撤计算、各切片分组正确、空样本降级。

---

## Phase 2 API / 前端

对齐现有分层 `api/routes/* → services/*`：
- `GET/POST /api/plans`、`GET/POST /api/journal`、`GET /api/portfolio`（当前持仓+敞口）、`GET /api/adherence`、`GET /api/trade-stats`
- 前端「交易层」规划补：计划录入/列表、交易日志表单、组合敞口面板、遵守度看板。
- 当前六入口 UI 的 `trade/orders` 占位页，在未来本阶段才填充为计划生成入口。

---

## Phase 2 完成标志

- 一笔交易可走完：信号 → 生成 `execution_plans` 计划 → 实际成交记入 `trade_journal`（带 plan_id）→ 自动算遵守度 → 进入实盘期望值统计。
- 能回答：「我这个月遵守计划的交易期望值是 +X，临时起意的是 -Y」——**这是系统从工具变成投研伙伴的临界点**。
- `services/portfolio.py` 能从日志重建当前持仓与敞口，供横切的组合视角（[05](05-cross-cutting.md)）消费。

---

## 已确认的输入（2026-07-04，原「待你确认」）

1. **交易记录录入方式：表单从零录入**。用户此前无正式交易记录，`trade_journal` 走前端表单录入，从零养成习惯。券商 CSV 导入与 Obsidian 解析两条路径按 YAGNI 从设计中删除（将来真有需求再加导入器，不影响表结构）。
2. **池子规模与风格：小池趋势波段（≤20 只）**，半导体/成长为主，持仓几天到几周。事件研究按整池聚合样本；区制定义用大盘 green/yellow/red 三态即可，不需要更细的风格分组。
3. **复盘关注点：四个坏习惯全部纳入** Phase 3 行为模式检测，每项对应一个可计算指标：

| 坏习惯 | 检测指标 | 依赖 |
|--------|----------|------|
| 追高进场 | 进场价距 20 日高点的分位分布 | trade_journal + OHLCV（Phase 1） |
| 过早止盈 | 卖出后 N 日标的续涨幅统计 | trade_journal + OHLCV |
| 死扛亏损 | 亏损单持有时长 / 盈利单持有时长之比；止损计划 vs 实际执行 | trade_journal + execution_plans |
| 计划外交易 | 无 plan_id 交易占比及其期望值 vs 有计划交易 | **共享主键闭环本身** |

第 4 项直接依赖 `execution_plans ⇄ trade_journal` 共享主键——这再次印证闭环是本阶段核心。
