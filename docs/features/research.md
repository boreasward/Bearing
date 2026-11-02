# 公司研究员与指定标的研究

> v2.13，随 `v2.15.0` 一次发布（发布日期 TBD，使用验收待做）。依据 [合并 spec](../specs/2026-11-01-v2.12-v2.15-five-layers-and-agent-roles-design.md) §6、D38–D43、D51。

## 做什么

公司研究员在首版提议者之前串行读取已入库的财季、手工指引、SEC 正文与命中、资讯和财报日；不给价格工具，不替提议者写买卖价位。运行版本为 `agent-4`，计划 schema 仍是 `agent-3`，旧链与旧计划按原口径读。

- `output.research={status,reason,output}`，status 为 `ok/rejected/error/disabled/carried`。有效 output 包含 1–12 条 facts、0–6 条 risks、0–6 条 blocked 与不超过 300 字的 summary；事实种类为 `disclosure/guidance/price_fact/user/inference`，事实 / 风险文本至多 200 字，inference 至多 3 条且须引非推断事实。引用只认研究阶段原 `call:N`；没取到写 blocked，不能写成不存在。
- 通过校验的原文作为 `[fact:research]` 给提议者和反方。首版研究成功时 thesis 至少引用一条研究 ref；研究拒绝或模型失败不阻断提议，记录「本轮无公司研究：<原因>」。同一 Recorder 编号连续，不重新编号。
- D42：只在首版 propose（批处理及 research 模式）调用研究员；复查修订、再次触发续期、补查重跑携带最近研究员段，状态 `carried`，不再调用研究员，首版引用规则不重复施加。
- 第二入口由你指定股票、问题和期限：先补采该 ticker 的七个 per-ticker 族，再冻结 cutoff、运行研究员 → 提议者 → 校验 → 反方，写 `research_versions`。**研究只出档案，不自动开链，不生成 trade_plan；action=buy 也只是结论。** 以后有规则信号时，可用 `get_research_dossier(ticker)` 读取截止时间前的档案。
- daily 未运行则即时后台提交，独立 900 秒，每天手动请求上限 5，token 计当日总额。daily 正在运行仍成功 **200 `{id,status:'queued'}`**，按 FIFO 由 agent 节点在单元之后、补查之前用剩余预算处理；之后 daily 开始不等已在跑的研究，靠族锁与共享 token 约束。
- 请求五态 `queued/running/waiting_data/concluded/closed`；版本可为 `concluded/waiting_data/error`。缺数据且 wait 留 `waiting_data`，后续队列再取。模型、额度、超时失败记错误版本并回 queued，三次失败关闭为 `max_attempts`；可手动结束，30 个自然日无新版自动结束。状态转换核对期望前态，不把请求改成 SQL 不允许的 error。
- 补查本轮不可见；Recorder 缓存，运行结束同事务落队列，事务后才采集。预算内有新行才新 cutoff 重跑至多一次；agent/review 共用处理器和剩余预算，研究入口也在预算内处理，见 [注册表](data-registry.md)。

## 页面

`#research` 的表格列标的、问题、状态、最新结论、截止与 token；新建表单提交前确认配额。`#research/<id>` 按版本切换，依次读「论点 / 事实与来源 / 催化 / 反证 / 价格与情景 / 下一步」，保留 `review_when`、耗时、token 和 cutoff。原校验输出及独立反方（`output.counter`，状态记在 `counter_status`：`ok` / `malformed` / `skipped` / `tool_rounds_exceeded`；超过 900 秒预算时反方 `skipped`，结论仍保留）用于阅读，催化 / 下一步没记录就写「本版未记录」，不补造内容。关联链只展示真实关联，不由档案 action 推导出新链。

「看推理过程」复用 AgentReasoning；`reading.steps` 在提议者前增加 `kind=research,title=公司研究,concept=agent.company_researcher`，携带 status/reason/output，引用跳到原 call 编号。批处理链内研究段不另建档案。

## 接口与命令行

| 接口 | 内容 |
| --- | --- |
| `POST /api/research` | `{ticker,question?,horizon_days?,request_id}` → `{id,status}`；同 request_id 回放，问题至多 500 字 |
| `GET /api/research` | `{items:[{id,ticker,question,status,latest_summary,cutoff_at,tokens,created_at}]}` |
| `GET /api/research/{id}` | `{request,versions,chain}`；versions 含 version_no/status/action/summary/cutoff_at/agent_run_id/output/review_when/tokens/elapsed_ms/created_at，chain 可 null |
| `POST /api/research/{id}/close` | 手动结束（填写理由） |
| `GET /api/workbench/agent-runs/{id}/reasoning` | 读取原运行推理与研究节 |

非法 ticker → 400 `invalid_ticker`；研究开关关闭 → 409 `feature_disabled`；每日配额用尽 → 409 `research_quota`；031 缺失 → 503 `migration_031_missing`。daily 占用不是本入口的 409 错误，和旧「重新运行 Agent」入口不同。

命令行：`python -m investment_assistant.analysis.research_requests run <request_id>`（前台跑一次排队中或等数据的研究请求）与 `show <request_id>`（只读，看请求和各版结论）；请求 id 取自研究页或 `GET /api/research`。`run` 会真实调用模型与采集，需要获准环境。

## 怎么验证一次

1. 获准后在研究页提交一只池外股票与问题，确认额度提示、返回 id、queued 读回与后续真实版本；同请求回放不重复创建。
2. 试非法 ticker、关闭开关、配额用尽；daily 在跑时应 200 入队而非显示创建失败。
3. 抽查 facts / blocked / counter / 原 call 引用与 cutoff，人工确认能看懂最近披露与风险。即使 buy，也核对没有新增计划或链。
4. 查看批处理首版研究、失败不阻断与 carried 修订；补查采集的新事实只能在新 cutoff 的新版本出现。

## 已知限制

首版池外数据可能不足；正文节选依标题正则，缺节返回 error，不由模型猜。研究排队受额度和预算限制，三次失败不是研究结论。可读档案与引用校验不证明投资有效性；策略 / 指标仍 Gate HOLD。真实端到端结果记录到 [未执行验收清单](../roadmap/v2.12-v2.15-acceptance.md)，不以页面夹具代替。
