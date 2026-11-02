# 数据适配器与标准指标注册表

> v2.12，随 `v2.15.0` 一次发布（发布日期 TBD，使用验收待做）。设计见 [合并 spec](../specs/2026-11-01-v2.12-v2.15-five-layers-and-agent-roles-design.md) §5、§24；真实备源探测见 [验收 §0](../roadmap/v2.12-v2.15-acceptance.md)。

## 做什么

数据族由 `data.registry` 登记；宿主负责目标、族锁、采集、落库、证据，模型只可登记补查。正式顺序及来源：

| 族 | 内容 | 主源 / 正式备源 |
| --- | --- | --- |
| `bars` | 日线、revision 与来源 | yfinance / 无（Stooq 未启用） |
| `news` | 资讯标题 | yfinance Search / 无 |
| `sec_filings` | SEC 申报列表、受理时间与 Item | SEC EDGAR / 无 |
| `earnings` | 下次财报日 | yfinance calendar / Nasdaq JSON |
| `fred` | 信用利差与收益率曲线 | FRED（需 key）/ 无 |
| `universe` | 纳指 100 范围文件 | file / 无 |
| `fundamentals` | SEC companyfacts 财季事实 | SEC / 无 |
| `sec_text` | 申报正文、文件索引与正文事实 | EDGAR archives / 无 |
| `sector` | SIC 两位行业组、ticker 覆盖 | EDGAR submissions / 无 |

- `Adapter.targets(conn, scope, *, explicit=None, facts=None, universe_loader=None)` 统一集合；daily 的 `TargetFacts` 由宿主原有池、持仓、模拟持仓、挂单和活跃链算法提供。日线另含 SPY / 行业 ETF / 纳指范围 / 活跃研究请求；财报日与行业组去掉 SPY 和行业 ETF；资讯、申报列表、财季、正文取池 ∪ 真实持仓 ∪ 活跃链 ∪ 活跃研究请求。单 ticker、研究、新成员、补查的显式范围不能扩成全池。
- `run_adapter(..., deps, features, tally=None)` 使用本次冻结开关。只有主源最终错误为 `timeout/network/http_5xx/http_429/empty`、有第二源且 `backup_sources=true` 才试一次备源；`http_4xx/parse/other` 不切。SEC/FRED/earnings 已有内部 policy，外层不重复套重试，`attempts` 合并真实主备尝试次数。存储或事务退出失败仍是失败，不因写库失败换源。
- 目标明细为原字段加 `source_used`（来源 id）、`fallback: {tried, reason}`；双源失败保留主源原因。每族每 scope 一行 `data_fetch_runs`；`sec_monitor` 正文记 `family=sec_text, detail.scope=sec_monitor`，collect 跳过已下载文件。历史缺来源的行不补造。
- SEC 正文每 ticker/session 最多发起 3 份下载；有效 `filings.output_dir` 及 `.budgets` 是全部入口共享的正文根与日预算事实，见 [运维](../operations/scheduling-and-notifications.md#sec-正文目录与日预算v212a2)。SIC 无匹配为未归类，不猜行业、不阻断。
- `request_data(family,ticker)` 只缓存请求，运行结束与 `agent_runs` 同事务写队列；返回「本轮不可见」。事务结束后，agent 与 review 尾部共用 handler 补采；剩余不足 120 秒为 `deferred`，只有 `fetched` 且新行数大于 0 才以新 cutoff 重跑，每单元每天至多一次。已开链走 recheck（`plan_reviews.detail.via=data_request`）；未开链再 propose，受原尝试上限约束。研究员段按 D42 carried，不再调用。
- 补查行的终态：`queued` 只在产生它的 daily 运行里由 agent / review 尾部结算。手动「重新运行 Agent」、`rerun_agent` 与手动复查 `recheck` 不在手动路径里同步补采，结束时把本次产生且仍 `queued` 的行结算为 `deferred`，原因 `manual_run`；daily 的 review 收尾再把「`run_id` 不是本运行、且 `requested_at` 早于本运行 cutoff」的遗留 `queued` 行结算为 `deferred`，原因 `orphaned`。所以任何一行最终都有终态，`workflow check` 的 `data_requests` 项按交易日窗口过滤 `requested_at`，不会被前几天的孤儿行永久拖成 mismatch。

标准指标是 `indicator-1` 注册项，按已知截止日现算、不落历史表；包装原函数，不统一口径、不改冻结数值：

| 指标 id | basis | window（读取根数） |
| --- | --- | --- |
| `atr14`、`atr_pct` | high_low（atr_pct 分母为 close） | 15 |
| `ma20_close`、`ma50_close`、`ma200_close` | close（Agent 工具） | 20 / 50 / 200 |
| `ma20_adj_close`、`ma50_adj_close`、`ma200_adj_close` | adj_close（规则 / 筛选） | 20 / 50 / 200 |
| `high_20`、`low_20`、`high_252`、`low_252` | high_low，含当日 | 20 / 20 / 252 / 252 |
| `avg_volume_20`、`volume_ratio`、`dollar_volume_20` | volume（成交额为 close × volume） | 20 |
| `rs20`、`rs60` | adj_close，相对 SPY | 21 / 61 |
| `screen_rs` | adj_close，相对 QQQ | 61 |
| `ret20`、`ret60` | adj_close | 21 / 61 |
| `breadth` | breadth，各 ticker / 指标独立样本资格 | None |
| `sector_strength` | adj_close，相对 SPY | 61 |

共 22 项。`compute(id,bars,*,session,benchmark_bars=None)` 过滤未来日线、排序、截窗；样本或所需基准不足为 `None`。读取端分别供应 SPY / QQQ，不能混用。旧 `get_indicators` dict 的键、顺序、类型与数值保留，close MA 仍映射旧键 `ma20/ma50/ma200`；独立推导 `derivation.chain` 不调注册表公式。详见 [指标注册表概念](../knowledge/concepts/indicator.registry.md)。

## 页面

「数据」`#data` 展示九族加一张「指标」卡，替代旧 ATR 族卡；卡上 `sources` 给来源口径、`today_sources={primary,backup,failed}` 给今日目标计数。族详情按 scope 看拉取记录与实际来源；日线浏览有 `source`，行业浏览有 SIC / group / source / fetched_at，财季列最近 8 季，正文列文件与事实摘要。指标卡列 basis、window、used_by 和概念入口。旧 ATR 响应仍兼容读取，不代表 ATR 仍是新注册数据族。其余操作见 [数据菜单](data-menu.md)。

## 接口与命令行

| 接口 | 内容 |
| --- | --- |
| `GET /api/data/families`、`/api/data/families/{id}` | 目录、覆盖与来源；特殊卡 `id=indicators, kind=derived, items=[{id,title,basis,window,used_by,concept}]` |
| `GET /api/data/indicators` | 标准指标登记清单 |
| `GET /api/data/browse/{id}?ticker=&series=` | 原始事实浏览，不触发采集 |
| `POST /api/data/families/{id}/fetch` | 原手动入口，可传池外 ticker；新增 earnings / fundamentals / sec_text / sector |

命令行（`fetch` 会真实拉取并写一行 `manual_family` 拉取记录，其余只读）：

```
python -m investment_assistant.data.registry list
python -m investment_assistant.data.registry fetch <family> [--ticker <ticker>]   # 逐只族必须给 --ticker
python -m investment_assistant.derivation.indicators list
python -m investment_assistant.derivation.indicators compute <id> <ticker>
```

`fetch` 对逐 ticker 的族要求 `--ticker`：全族目标集在 `workflow.targets`，数据层不向上引用（裁决 R31）；`list`、`--help` 不碰数据库。

## 怎么验证一次

1. 在获准、迁移 031 就绪的环境打开数据页，核对九族顺序、指标 22 项及两套 MA / 两个基准；保留 Gate HOLD 提示。
2. 单只拉取 earnings，在可控主源失败场景核对 Nasdaq 的 `source_used`、fallback 原因、真实 attempts 与落库回执；双源失败不能显示成功。关备源后不切源。
3. 核对 bars 正式仅有 yfinance；Stooq 404 结论不能用夹具解析通过替代。补缺机制的离线案例验证最近 10 个交易日仅无任何 revision 的日期可写、主源恢复追加 revision；不能写成在线 Stooq 验收通过。
4. 核对补查只在新一轮可见、有新行才重跑，运行详情显示五计数 `{queued,fetched,failed,deferred,reruns}`。

## 已知限制

Stooq 解析与补缺机制已有独立实现，但 Task0 实测不可用、未正式注册，当前 bars 没有备源。若将来启用，`adj_close=close×主源最近比例` 只是近似，无主源取 1，仅补最近 10 个交易日的缺口；拆股口径仍待真实源验证。Nasdaq ETF 可返回 `data=null`，盘前 / 盘后 time 不可靠；Nasdaq 返回日期 `unknown`（未公布）按空结果失败处理（`FetchError(kind='empty')`），目标按备源也失败记账并保留主源的失败原因，不把「未公布」当作成功日期。指标窗口不足如实为空；注册与算术一致不证明策略有效，仍为 Gate HOLD。
