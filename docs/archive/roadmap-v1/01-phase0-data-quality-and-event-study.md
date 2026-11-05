# 01 — Phase 0：数据质量地基 + 事件研究框架

> **已归档（2026-10-23）**：本文属于 roadmap v1，已被 [roadmap v2](../v2/roadmap/roadmap.md) 取代，仅作历史参考，不再更新。

> 目标：用**最小成本**拿到「我关注的信号到底有没有边际信息」的诚实答案。这是「先证伪、再投入」原则的落地——如果信号没用，后面的交易计划和复盘就只是把噪声包装成叙事。
> 依赖：现有 `data/price.py`、`market/service.py`（已支持 as-of 日期）、`tickers/trend.py`、`strategies/trend_relative_strength.py`。
> 历史估算：1–1.5 周，不是本轮新排期。

## 2026-10-17 评审状态

事件研究框架、FastAPI/CLI/UI 入口已存在；本轮 as-of 扫描、历史评分、成熟度、精确端点和缓存语义修复进入最终复核。默认研究窗口为 5/10/20 交易日，MA 是 SMA20，不是 21EMA。RS 旧阈值 1.2 的收益差单位/意图未确认，EPS/VCP 无可用事件源，不能把它们写成已测零事件。

[只读归档评审](reports/2026-10-17-phase0-review.md)从八只股票重建 132 个 SMA20 事件，5/10/20 有效 n=131/129/122，均值为 2.3453/2.9158/5.8974 个百分点。**全部正式 Gate HOLD**：未校正 CI 不足以证明信号有效，可信历史信号、实际区制、样本依赖、选择偏差、PIT 和成本/入场模型仍有缺口。

当前 DB 回环连接不可达（`OperationalError`，不记录 DSN），当前表数量 unknown 而非 0；未做 HTTP/UI 验收。只读原始 hash 与财务样例复算不等于全库验收。最终变更测试数字待 [实施状态](implementation-status-2026-10-17.md)回填。下文保留原任务设计，现行使用以 [事件研究说明](../v2/features/legacy/README.md)为准。

---

## 为什么 Phase 0 排第一

- 工程量小，但回答的是**最贵的问题**：「值不值得在这套信号上继续投入」。
- 事件研究可池化跨标的观测，但不自动解决小样本与相关性；有效独立样本设计仍是 Gate 前置。
- 它强制你先解决**复权 / point-in-time** 这个隐形地基——否则后面所有回测和复盘都建在污染数据上。

---

## Task 0.1 — 数据质量地基（复权 + point-in-time 取价）

**问题**：yfinance 默认返回的价格在 split / 分红处理、退市、改名上不可靠。事件研究若用了未复权或错误复权的价格，PEAD 这种 +5/+20 日量级的效应会被噪声完全淹没。

**做什么**：

1. **统一复权口径**：取价层显式使用 `auto_adjust=True`（或保留原始 + 复权两列），并在取到数据后做 split 跳变校验（相邻日 |收益| > 50% 且非财报日 → 告警，疑似未复权 split）。
2. **point-in-time 取价器**：抽象一个 `as_of` 取价接口 `get_price_history_asof(ticker, end_date, days)`，对历史每个事件日只返回 `[:end_date]` 的切片。**这是防前视偏差的关键基础设施**，Phase 0/2 都依赖它。
   - 现状已有雏形：`market/service.py:_default_price_fetcher_until(ticker, days, target_date)` 已实现按目标日期切片，将其提炼为 `data/price.py` 的公共函数，供事件研究与回测共用。
3. **交易日历**：引入交易日历（`pandas_market_calendars` 或最简自维护 NYSE 假日表），让「+5 日」是 5 个**交易日**而非自然日，且正确跳过停牌 / 假日。

**测试**（全 mock，离线）：
- 给一段含 2:1 split 的 mock 价格，断言复权后相邻收益无 ~50% 跳变。
- 断言 `get_price_history_asof(t, '2024-03-01', 60)` 不含 `2024-03-01` 之后的任何 bar（前视偏差守卫）。
- 断言 +5 交易日在跨假日时落到正确日期。

**交付**：`data/price.py` 增 `get_price_history_asof` + split 校验；可选 `data/calendar.py`。

---

## Task 0.2 — 事件研究引擎（A 层）

**接口**：

```
investment_assistant/research/event_study.py

def run_event_study(
    events: list[Event],            # Event(ticker, date, kind, meta)
    *,
    horizons=(5, 10, 20),           # 前瞻交易日，现行默认
    asof=None,                     # 同时约束事件与收益成熟度
    benchmark="SPY",                # 计算超额收益的基准
    price_fetcher=...,              # 注入，便于测试
) -> EventStudyResult
```

**`Event` 的来源（复用现有信号，不新造轮子）**：

| 事件类型 `kind` | 触发定义 | 来源 |
|----------------|---------|------|
| `eps_beat` / `eps_miss` | 未来 EPS 实际 vs 可信历史一致预期 | 当前不可用，无占位事件可供正式评审 |
| `rs_strong` | 20 日收益差小数；旧 1.2 单位/意图未确认 | 挂起，不改阈值寻优 |
| `ma_reclaim` | 相邻交易日收盘价由不高于 SMA20 转为高于 SMA20 | `tickers/trend.py`；不是 21EMA，来源须核验 |
| `vcp` | 波动收缩事件 | 当前事件源未实现 |
| `score_high` | 可信 `trend_relative_strength` 评分达到阈值 | 定义已有；本次无可信历史评分/宏观输入，未计算 |

**输出 `EventStudyResult`（统计诚实是重点）**：

```
{
  "kind": "ma_reclaim",             # 仅为结构示例，不是本轮实测结果
  "n": 37,                          # 样本量（必报，太小直接标注不可信）
  "horizons": {
    "5":  {"mean_excess": 0.018, "median": 0.012, "hit_rate": 0.62,
           "t_stat": 1.9, "ci95": [-0.002, 0.038], "std": 0.05},
    "20": {...}
  },
  "by_regime": {                    # 区制分层（连接目标①），见 04
    "green": {...}, "yellow": {...}, "red": {...}
  },
  "caveats": ["n<30, 结论不可信", ...]   # 自动生成的诚实警告
}
```

**统计纪律（硬性要求，写进实现）**：
- **超额收益**而非绝对收益（减基准同期收益），否则你测的是大盘 beta 不是信号。
- **每个结论必带 `n` 和置信区间**；`n < 30` 自动加 `caveats` 警告。
- **区制分层**：每个事件按 `compute_market_signal_for_date(event.date)` 打 green/yellow/red 标签，分层统计。
- **不做参数寻优**：Phase 0 只描述「现有信号的前瞻分布」，不调阈值、不反推权重（那是过拟合，见 [00 §2](00-principles-and-direction.md)）。

**测试**：
- 构造已知前瞻收益的 mock 价格，断言 `mean_excess` / `hit_rate` 计算正确。
- 断言基准扣减正确（信号收益 = 个股 - SPY）。
- 断言 `n` 小样本时 `caveats` 被填充。
- 断言区制标签调用了 `compute_market_signal_for_date`（mock）。

**交付**：`research/event_study.py` + 测试；可选 `event_studies` 表（迁移 012）缓存结果。

---

## Task 0.3 — 事件研究报告入口（先 CLI，后接前端）

**做什么**：
- `python -m investment_assistant.research.event_study --kind eps_beat --since 2022-01-01`，打印各 horizon 的均值 / 命中率 / 置信区间 / 区制分层表。
- 不急于接前端；Phase 0 的目的是**给你（和我）一个判断依据**：哪些信号值得在 Phase 1 投入数据采集，哪些该砍。

**与前端的衔接**：报告数据结构对齐 `EventStudyResult`，Phase 3 可视化时直接喂给 ECharts 画前瞻收益分布柱状图 / 按区制的小提琴图（见 [05](05-cross-cutting.md)）。

---

## Phase 0 的决策门（Gate）

跑完事件研究后，对每个信号做一次**去留判断**：

| 结果 | 行动 |
|------|------|
| `n` 充足 + 超额收益显著（CI 不跨 0）+ 跨区制稳健 | **保留**，Phase 1 优先为它补数据，Phase 2 可入交易计划 |
| `n` 充足但超额收益不显著 | **降级**，不作为独立决策依据，可作辅助 |
| `n` 太小 | **挂起**，扩大池子 / 拉长历史再测，不下结论 |
| 仅在单一区制有效 | **条件化保留**，只在该区制启用（连接目标①） |

> 这一步是整个系统「诚实」的体现：让数据决定后面投入哪里，而不是凭感觉全都做。

---

## 产出物清单

- `data/price.py`：`get_price_history_asof` + 复权校验
- （可选）`data/calendar.py`：交易日历
- `research/event_study.py`：事件研究引擎 + CLI
- （可选）`migrations/012_event_studies.sql`：结果缓存表
- 一份**事件研究首轮报告**（写入 `docs/roadmap/reports/` 或作为 PR 描述），作为 Phase 1 取舍依据
