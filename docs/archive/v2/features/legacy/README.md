# v1 遗留功能（已于 v2.1.0 删除）

roadmap v1 的遗留功能在 v2.1.0 统一收拢：没有价值的整体删除，仍有价值的并入工作台。**旧实现见 tag `v2.0.0`。**

| 功能 | 去向 |
| --- | --- |
| Hermes 宏观分析、决策证据（DeepSeek） | 删除（`hermes/`、`services/hermes.py`、`/api/hermes/*`、「交易」页） |
| SPY/VIX 市场信号 | 删除（`market/`、`market_signals` 表、`/api/market/*`、「数据 → 信号总览」；决策单元不再带 `market_status`） |
| 个股趋势快照、「技术面趋势」页 | 删除（`tickers/`、`ticker_signal_snapshots` 表、`/api/tickers/*`）；K 线图（MA20/50/200 + 成交量）并入「标的」页 |
| 策略评分 | 删除（`strategies/`、`strategy_scores` 表、`scores` 任务、`/api/strategies/*`、「策略 → 策略评分」） |
| 事件研究 | 删除（`research/event_study.py`、`event_studies` 表、`event_study` 任务、`/api/research/event-study/*`）；上线前检查升级为 `backcheck-2`，增加信号后 5/10/20 日相对 SPY 的超额统计，见 [pool-and-context.md](../pool-and-context.md) |
| 研究观察卡 | 删除（`research/cards.py`、`research_observation_cards` 表）；关注列表的「下一次事件」改读财报日 |
| 美股财季数据、指引 | 并入「标的 → 基本面」，见 [fundamentals.md](../fundamentals.md) |
| SEC 文件记录 | 移到「更多 → 工具 → SEC 文件」，见 [sec-filings.md](../sec-filings.md) |
| 推送路由的 `metrics`、`scores` 与 `daily` 频道 | 删除；已存设置在读取时过滤，路由失效返回结构化错误 |

旧地址（`#data/...`、`#technicals/...`、`#strategy/...`、`#trade/...`）回落到「今日」。六张遗留表由[迁移 022](../../../../operations/migrations-and-backup.md) 删除，`job_reports` 历史行与已冻结的 `backcheck-1` 记录保留。

## 五层搬家：退役的美股研究同步入口

`us_research sync` 与 `tasks/us_research.py` 已删除；`POST /api/us-research/sync` 返回 **410** `{"error":"use_data_menu"}`，手动采集入口统一到「数据」菜单。财季、指引、申报的 GET 保留，实现在 `analysis/fundamentals.py`；行情改读已入库的 `price_bars`，不再使用旧 market dataset 或发起在线行情采集。SEC 成功归档后的本地研究刷新仍保留，实现在 `data/acquisition.py`。
