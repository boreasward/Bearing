# Roadmap — 投研 + 量化投资系统

> **已归档（2026-10-23）**：本文属于 roadmap v1，已被 [roadmap v2](../v2/roadmap/roadmap.md) 取代，仅作历史参考，不再更新。

> 创建：2026-06-30 ｜ 状态校准：2026-10-17（基线 `6fd63f8`）
> 本目录保留长期方向与历史设计；**当前交付和验收状态**以 [实施状态](implementation-status-2026-10-17.md)及 [Phase 0 评审](reports/2026-10-17-phase0-review.md)为准。历史周数是原估算，不是本轮新排期。

本轮仅做代码实现与现有数据只读核验，不补采、不写真实数据库、不通知、不交易、不部署、不 commit/push。FastAPI、邮件 B10、六入口 UI 与 10 月跨市场研究支线已落地；本轮工程修复仍在最终复核，不能提前写成最终测试通过。全部正式信号 Gate 为 HOLD，当前 DB / HTTP / UI 未验收，跨市场用户验收仍待完成。

---

## 0. 一句话定位

> **这套系统的护城河不是「又一个量化回测引擎」——以你几十只关注股的样本量，在那个赛道上统计意义上赢不了；护城河是「把我的真实交易决策、当时的市场状态、事后结果，闭环起来做归因」。这是只有你自己的数据才能做、且绝大多数工具都没有的东西。**

工程重心应压在这个**闭环**上，而不是平铺去追求「全自动 alpha 工厂」。系统定位为**决策支持 + 纪律审计**，不做自动下单（这一点现状已经做对）。

---

## 1. 阅读顺序

| 文档 | 内容 | 给谁看 |
|------|------|--------|
| [08-living-collaboration.md](08-living-collaboration.md) | 当前切片、调度边界和每周回顾 | **先看近况**；阶段原则仍先读 00 |
| [09-long-term-decision-loop.md](../v2/roadmap/roadmap.md) | 2026-10-23 逐项讨论后确认的长期路线：数据 → 交易计划 → 复盘，P1–P4 分阶段，含决策记录 | 长期方向；各阶段开工前另写 spec |
| [工作台形态](workbench-shape-2026-10-17.md) | 五层形态，以及从 vibe trading 项目吸收和拒绝的部分 | 长期草案，不代替实施状态 |
| [00-principles-and-direction.md](00-principles-and-direction.md) | 方向判断、量化陷阱（过拟合/小样本/幸存者偏差）、护城河论证、系统定位 | **先读这篇**，它解释了后面所有优先级排序的依据 |
| [01-phase0-data-quality-and-event-study.md](01-phase0-data-quality-and-event-study.md) | 数据质量与事件研究：框架已落地，本轮修复最终复核；归档探索已评审 | **全部正式 Gate HOLD** |
| [07-phase-r-fastapi-migration.md](07-phase-r-fastapi-migration.md) | FastAPI 迁移的历史设计与完成边界 | `engineering_done`，不是当前待迁移阶段 |
| [02-phase1-data-foundation.md](02-phase1-data-foundation.md) | 通用 OHLCV / 广度 / FRED / EPS surprise 长期设计；本轮仅输入可追溯性最小补强 | 长期项未完成；015 未执行到真实 DB |
| [03-phase2-plan-execution-loop.md](03-phase2-plan-execution-loop.md) | 计划—执行—复盘闭环：`execution_plans` 与 `trade_journal` 关联 | 未落地，系统价值核心 |
| [04-phase3-review-and-regime.md](04-phase3-review-and-regime.md) | 跨笔行为、三方业绩、区制条件化及 LLM 校准 | 未落地 |
| [05-cross-cutting.md](05-cross-cutting.md) | 组合敞口、可视化、文档、安全、CI | 按实际依赖推进 |
| [06-backlog-quick-wins.md](06-backlog-quick-wins.md) | 小项清单及依赖；B10 邮件已完成 | 不是自动授权的执行队列 |

---

## 2. 与旧 roadmap 的关键差异（自我修正）

第一版 roadmap（对话中给出）有几个量化新手易犯的错，本目录已纠正：

| 旧版主张 | 问题 | 修正 |
|----------|------|------|
| 「回测引擎是核心分水岭」 | 几十只股 + 判别式打分做组合回测，样本量不足 + 幸存者偏差 + 过拟合，结论是噪声 | **回测降级**：事件研究（A）优先 → 横截面 IC（B）次之 → 全组合回测（C）最后且谨慎。见 [00](00-principles-and-direction.md#二把回测拆成三个严肃度层级) |
| 「用回测结果反推权重」 | 这是在**保证过拟合**（数据窥探 / 多重检验） | 删除该做法；权重只在样本外、跨区制稳健时才微调 |
| 「FRED 宏观排在很靠前」 | 宏观择时历史战绩差，CPI/就业是滞后数据，对择时基本是噪声 | **市场广度 > HY 信用利差 > 收益率曲线 >> CPI/就业**；优先用已有价格数据算广度 |
| 「计划」与「复盘」是两条独立主线 | 它们是同一个闭环的两端 | 合并为 **Phase 2 计划-执行-复盘闭环**，共享主键 |
| 复盘 = 给单笔交易写点评 | 价值低 | 复盘 = **跨笔行为模式识别** + 实盘期望值 + 三方对比 |
| （缺失） | 没有组合/敞口视角 | 新增：组合敞口是投研第一视角，见 [05](05-cross-cutting.md) |
| （缺失） | LLM 输出无法问责 | 新增：**LLM 校准追踪**（Brier score），见 [04](04-phase3-review-and-regime.md) |

---

## 3. 阶段总览与依赖

阶段顺序表达依赖，不代表按周自动推进。状态允许并存：`engineering_done` 仅表示代码/入口已落地，`gate_hold` 表示研究证据未通过，`runtime_unverified` 表示本轮真实运行未验收，`user_acceptance_pending` 表示仍待用户使用验收。

| 阶段 / 支线 | 2026-10-17 状态 | 下一步前置证据 |
| --- | --- | --- |
| Phase 0 | 框架已有；本轮修复已通过离线回归；归档探索完成，`gate_hold` | 可信历史输入、实际区制、独立样本设计及成本/入场模型，而非只看 CI |
| Phase R | `engineering_done` | 不重做迁移；当前环境 HTTP/UI 为 `runtime_unverified` |
| 10 月跨市场研究支线 | `engineering_done` + `user_acceptance_pending` | 保留来源/缺口说明，用户手动检查工作流；不冒充通用 Phase 1 |
| Phase 1 最小补强 | 015 来源日期/版本隔离已通过离线测试；真实迁移 `runtime_unverified` | 老行 null、不回填、冲突保留；真实执行另授权，先在可丢弃 DB 验证 |
| 长期 Phase 1 | 通用 OHLCV / 广度 / FRED / EPS surprise 未完成 | Gate 证据与明确范围授权；当前不扩采 |
| Phase 2 / 3 | 未落地 | 计划/日志、可信价格/信号及真实使用记录；不以研究卡代替闭环 |

当前修复已完成离线回归（后端 749 通过 / 52 跳过 / 5 个环境性失败，前端 287 通过），下一步是在可连接环境只读核查真实库、按手动入口验收。正式 Gate 不通过不等于自动砍掉信号，而是没有足够证据作去留决定。Phase 1 只做评审明确暴露的输入可追溯性最小补强，不开启 FRED、一致预期或全组合回测。闭环仍是长期价值核心，但它也不能免除记录缺失、计价和选择偏差检查。

---

## 4. 迁移编号约定

以仓库实际文件为准，不能把历史预占编号当已建表或迁移验收证据。

| 迁移 | 实际用途 | 当前边界 |
| --- | --- | --- |
| 009 | `event_studies` 缓存 | 已有实现，当前库覆盖未知 |
| 010 | `job_reports` 目标日期 | 已有实现 |
| 011 | SEC `filings` | 已有实现，不是 EPS surprise |
| 012 | 通知渠道 | 邮件 / Discord 配置已落地 |
| 013 | A 股研究 | 不是 `execution_plans` |
| 014 | 美股研究 | 不是 `trade_journal` |
| 015 | `signal_provenance` 最小补强 | 本轮新增文件，离线测试通过；未对真实 DB 执行；不是 `llm_predictions` |

通用 OHLCV、广度、FRED、EPS surprise、plans / journal、LLM 校准的未来迁移编号均待实施时分配，不再预占已用编号。015 老行新增字段为 null 且不回填；本轮不运行生产迁移。

---

## 5. 落地纪律（沿用仓库现有约定）

- **TDD**：新增逻辑必须先写测试；外部依赖（DB / 网络 / LLM）全部 mock 或注入，离线可跑。
- **分层**：`api/routes/*` → `services/*` → `db.py` / `tasks/*`，路由层薄封装。
- **降级不伪造成功**：无数据库配置、数据库不可达、事件源不可用与真实空样本必须区分；不可达不等于零记录，失败不应被吞成成功。
- **不静默吞错**：外部失败结构化返回 + 日志，敏感凭据和 DSN 不进入报告。
- **配置不是交付**：`MacroConfig` / `FundamentalsConfig` / `PriceConfig` 等配置存在不代表采集已实现；`BacktestConfig` 已用于事件研究，默认窗口为 5/10/20。

---

## 6. 已确认的输入（2026-07-04）

三个输入已确认并写入 [03「已确认的输入」](03-phase2-plan-execution-loop.md#已确认的输入2026-07-04原待你确认)：

1. **交易记录现状：基本没正式记录** → `trade_journal` 走表单从零录入；CSV 导入 / Obsidian 解析按 YAGNI 删除。
2. **标的池：小池趋势波段（≤20 只）**，半导体/成长为主 → 事件研究整池聚合，区制用大盘三态。
3. **复盘关注点：四个坏习惯全要**（追高进场 / 过早止盈 / 死扛亏损 / 计划外交易）→ 各对应一个可计算指标，见 03 的映射表。

另：交付硬性标准（手动入口 + 验证入口 + 功能说明 + 如何验证示例）已升级为 [00 §6 DoD](00-principles-and-direction.md#6-交付硬性标准dod)，横切贯穿所有阶段。
