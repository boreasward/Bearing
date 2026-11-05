# 04 — Phase 3：复盘引擎 + 区制条件化 + LLM 校准

> **已归档（2026-10-23）**：本文属于 roadmap v1，已被 [roadmap v2](../v2/roadmap/roadmap.md) 取代，仅作历史参考，不再更新。

> 目标：把 DeepSeek + Hermes 从「泛泛宏观解读」聚焦到你真正想要的「拿我的交易记录做归因复盘」。复盘的火力在**跨笔行为模式识别**和**三方业绩对比**，不是单笔点评。
> 依赖：Phase 2 的 `trade_journal` / `execution_plans` / 期望值统计；Phase 0 事件研究基线；现有 `hermes/deepseek_client.py`（已修好真实模型 ID）、`hermes/agents.py`（agent 框架）、`hermes/run_log.py`。
> 历史估算：1.5–2 周，不是本轮新排期。

**2026-10-17：本阶段未落地。** 下文是依赖 Phase 2 真实计划/日志的长期设计，不能把已有 Hermes 宏观分析或观察卡称为行为复盘。Phase 0 虽有分层输出接口，本次归档无可信 VIX/历史区制，全部为 `unknown`，不能生成可执行的区制结论。015 已用于本轮信号来源最小补强，LLM 校准迁移编号待未来实施时分配；本轮不调用新增 LLM 流程或扩大采集。

---

## Task 3.1 — 跨笔行为模式识别（复盘核心）

**正确用法**：不是「给这笔交易写段点评」（价值低），而是在几十上百条日志上**找模式**。

### 3.1.1 确定性指标先行（不靠 LLM）

在喂 LLM 之前，先用代码算出可量化的行为偏差——LLM 负责解释，不负责计算：

| 行为偏差 | 检测指标 | 出处 |
|---------|---------|------|
| 处置效应（截断利润/放任亏损） | 平均持盈时间 vs 持亏时间、盈利平均 R vs 亏损平均 R | disposition effect |
| 高波动过度交易 | 按 VIX/regime 分桶的交易频次 | |
| 追高 | 进场点相对近期高点的位置分布 | |
| 同类信号反复亏损 | 按 `source_signal` 分组的期望值（来自 Phase 2.4） | |
| 不遵守计划 | 遵守 vs 不遵守的期望值差（来自 Phase 2.3） | |
| 行业/标的集中亏损 | 按 ticker/行业分组的盈亏 | |

`services/behavior_metrics.py`：输出结构化偏差指标 + 证据交易列表。

**测试**：构造已知模式的 mock 日志（如全部盈利早退、亏损死扛），断言处置效应指标被正确识别。

### 3.1.2 LLM 归因（DeepSeek reasoner）

`hermes/review_agent.py`：
- 输入：3.1.1 的结构化偏差指标 + 代表性交易样本（reason/emotion 文本）+ 当时 regime。
- 用 `deep_research_model`（`deepseek-reasoner`）做归因叙事：把数字翻译成「你在 X 情形下倾向 Y，证据是这几笔，建议 Z」。
- 复用现有 `agents.py` 的 agent 配置框架（`data_sources` 加 `trade_journal`，`tools` 加 `behavior_review`）。
- 调用走**后台任务 + run_log 轮询**（现有 `tasks/runner` + `hermes/run_log.py`），不阻塞请求线程。

**纪律**：LLM 只做解释，所有数字来自 3.1.1 的确定性计算。结构化输出 `{patterns:[{name, evidence_trade_ids, severity, suggestion}], summary}`，不让 LLM 自由发挥编数字。

**测试**：注入 FakeDeepSeekClient 返回固定 JSON，断言 prompt 含真实偏差指标、输出被结构化解析、LLM 失败时降级为「仅确定性指标」不崩。

---

## Task 3.2 — 三方业绩对比

**回答「我的操作到底值不值」**，把实盘放进参照系。

`services/performance_compare.py` 对比三条权益曲线：

| 对照方 | 含义 | 数据来源 |
|--------|------|---------|
| **你（实盘）** | 真实交易的权益曲线 | Phase 2.4 实盘统计 |
| **持有 SPY** | 同期无脑买基准 | `ohlcv_bars` SPY |
| **策略理论最优** | 若严格按计划执行（遵守所有 entry/stop/target） | `execution_plans` 模拟成交 |

输出：三条曲线 + 关键指标对比（总收益、Sharpe、最大回撤、胜率）。

**最有杀伤力的两个差值**：
- 「你 vs 策略理论」= **执行损耗**（你的手动操作比按计划差多少）。
- 「你 vs SPY」= **是否值得主动交易**（没跑赢就该反思要不要继续选股）。

**统计诚实**：标注样本期、是否单一区制、交易笔数；笔数少时明确「不足以下结论」。

**测试**：三条曲线计算、执行损耗 = 你 - 理论、空交易降级。

---

## Task 3.3 — 区制条件化（连接目标①和②）

**做什么**：所有 Phase 0/2/3 的统计都支持**按 regime 分层**报告。

- 事件研究（Phase 0）已带 `by_regime`。
- 实盘期望值（Phase 2.4）按 regime 切片。
- 行为指标（Phase 3.1）按 regime 分桶。

**产出可执行结论**，例如：
- 「RS 策略在 green 区制期望 +X，在 red 区制 -Y」→ red 区制关闭该信号。
- 「我在 red 区制的交易频次是 green 的 2 倍但期望为负」→ 高波动该减少交易。

> 这把目标①（市场状态）从一个孤立的「灯」变成**所有策略与复盘的条件变量**，是系统内聚的关键。regime 标签在 Phase 1 多因子门控升级后更可信。

**测试**：分层聚合正确、某区制无样本时跳过不报。

---

## Task 3.4 — LLM 校准追踪（规划，编号待定）

**为什么**：只要系统/Hermes 输出带概率的判断（「70% 守住 21EMA」「conviction 80」），就该记录并事后验证。否则 LLM 只会产出**自信的废话**，你无法知道它准不准。

**未来 `llm_predictions` 迁移草案（编号待定）**：

```sql
CREATE TABLE IF NOT EXISTS llm_predictions (
  pred_id      BIGSERIAL PRIMARY KEY,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  source       TEXT NOT NULL,          -- macro_analyst / plan_builder / review_agent
  ticker       TEXT,
  claim        TEXT NOT NULL,          -- 可验证的断言文本
  probability  NUMERIC,                -- 模型给的概率 0-1
  horizon_days INT,                    -- 多久后可验证
  resolve_date DATE,                   -- 何时揭晓
  outcome      BOOLEAN,                -- 事后填：是否应验（NULL=未揭晓）
  resolved_at  TIMESTAMPTZ
);
```

**做什么**：
- 凡是带概率/conviction 的输出，落一条 `llm_predictions`。
- 定时任务（接现有 scheduler）到 `resolve_date` 用 `ohlcv_bars` 自动判定 `outcome`。
- `services/calibration.py` 算 **Brier score**（`mean((prob - outcome)²)`）和**校准曲线**（预测 70% 的事件实际发生率是否接近 70%）。

**价值**：让 DeepSeek 可问责。若校准差（说 80% 实际只对 50%），就给 LLM 输出打折扣或调 prompt。这是 LLM 驱动系统少有人做、却决定可信度的一环。

**测试**：Brier 计算、未揭晓的预测不计入、自动判定用收盘价正确、分桶校准曲线。

---

## Task 3.5 — 复盘报告编排

把上面汇总成一份定期复盘（周/月）：
- `tasks/review.py`：接现有 scheduler（如每周一次），汇总行为模式 + 三方对比 + 区制分层 + LLM 校准 → 一份结构化报告。
- 经 `_harness` 写 `job_reports` + 推 Discord（复用现有通知链路）。
- 前端「交易层/复盘」页展示，配 ECharts（三方权益曲线、行为偏差雷达、校准曲线）。

---

## Phase 3 完成标志

- 系统能主动告诉你：「你这段时间的坏习惯是 X（证据 N 笔），实盘比 SPY 差 / 好 Z，比你自己的计划差 W（执行损耗），且这些问题集中在 red 区制。」
- DeepSeek 的每个概率判断都被追踪，Brier score 让你知道它到底值不值得信。
- 目标③（复盘）从「不存在」变成系统**最有差异化的能力**——这正是 [00 §4](00-principles-and-direction.md) 定位的护城河兑现。
