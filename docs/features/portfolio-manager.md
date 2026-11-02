# 组合检查与组合经理

> v2.14，随 `v2.15.0` 一次发布（发布日期 TBD，使用验收待做）。设计见 [合并 spec](../specs/2026-11-01-v2.12-v2.15-five-layers-and-agent-roles-design.md) §7、§9、D49。关闭路径按 K2：关闭整个 portfolio 节点。

## 做什么

`review → portfolio → accounts`。开关开启、交易日时，对真实账户和每个活跃模拟账户各算一份 `portfolio-check-1` 硬结果；`execution.portfolio.check(*,session,plans,positions,pending,cash,equity,params,sectors)` 是纯函数，不调模型、不读库。

- verdict / order / 互斥只给**入场期链头版本**；持仓链只计敞口与只数。宿主按 `chain_signal_date` 升序、再 ticker 贪心装入；这是硬约束顺序，不是投资优劣排名。现金不够为 `deferred_by_cash`，order 为 null。
- 行业敞口含持仓、在挂与候选，除以本账户权益。`sector_cap` 默认 0.35、范围 0.05–1.0；`max_per_sector` 默认 3、整数 1–10，都是账户参数，不从历史拟合。未知 SIC 单列「未归类」，不计入任何行业组上限，`sector_unknown` 只提示。
- 结果含 `exposure_by_sector/verdicts/conflicts/order/params_used/version`。真实账户另可调用一次 `agent-pm-1`，独立 300 秒，commentary 为 summary/ranking/tradeoffs/hold_actions；ranking 只能包含硬判定 ok 的计划，模型不得改价位或股数。模拟账户不另跑 PM，Agent 自管用 `agent-portfolio-2` 的第八段 `[fact:portfolio_check]`。
- 买单被硬判 blocked 时取首个 reason 拒绝；`deferred` 本身不是硬拒绝，原现金等订单校验照常。照计划执行对照账户不读取组合检查，仍为 `follow-plans-1`。检查不改写或作废单条计划，真实账户不自动成交。

| 五个新增硬拒绝码 | 精确中文 |
| --- | --- |
| `sector_cap` | 超过行业集中度上限 |
| `max_per_sector` | 超过同行业标的数量上限 |
| `single_cap` | 超过单笔风险或单票敞口上限 |
| `total_cap` | 超过组合总敞口上限 |
| `held_ticker` | 已持有、在挂或重复的标的 |

`sector_unknown=行业未归类`、`deferred_by_cash=现金不足，暂缓执行` 是提示 / 暂缓，不能放进硬拒绝枚举。

| 条件 | 节点、订单与页面 |
| --- | --- |
| 冻结 `portfolio_manager=false` | 整节点 `skipped/feature_disabled`，**不执行硬检查**；fact 写「本版未检查」，`validate_orders(None)` 不作组合拦截，原现金 / 股数校验及 accounts 照常；Today `not_computed` |
| 开启且硬结果已产出，随后 PM 模型失败 / 不可用 | 硬结果保留、无成功 commentary，Today `degraded` /「只有硬结果」，已有本账户硬结果仍约束订单 |
| 开启但缺硬结果 | `not_computed`，不是空组合成功，也不是已有硬结果的 degraded；节点失败下游 accounts 照常，缺行不作组合拦截 |
| 硬结果与成功意见完整 | `ready` |

## 页面

「今日」运行一览后为组合检查块：总结、按 order 的判定表、行业敞口、互斥组、经理建议。状态文字：ok「可执行」、blocked「被拦截」、deferred「暂缓」。运行详情 portfolio 按账户列计数，展开取该账户 / session 的硬结果与意见；缺行明确「没算」。

账户参数表单新增两项；决策行显示真实 `agent_version`。账户对比及概览权益图的 `markers` 为 feature（功能开关，虚线）/ version（版本，实线）/ params（参数，点线），图下列交易日、种类和说明。分界保留改变记录，不用曲线断点推测。每日推送在「🆕 新计划」之后新增「🧩 组合检查」。

## 接口与命令行

- `GET /api/workbench/today` 的 `portfolio_check` 与 `GET /api/accounts/{id}/portfolio-check?session=` 复用 `{status,reason,session,cutoff_at,result,commentary}`；缺行 `not_computed`，result/commentary 可空，真实账户元信息可附带。
- `POST /api/accounts/{id}/params` 原请求 `{request_id,params,reason}` 接受两项参数；按账户参数原规则下一交易日起生效。
- `GET /api/accounts/compare`、`GET /api/accounts/{id}/equity` 增加 `markers:[{session,kind,label}]`；`GET /api/accounts/{id}/decisions` 每项增加 agent_version。
- `GET /api/runs/{id}/detail` 的 `portfolio.results.kind=portfolio`，items 为 `{account_id,name,ok,blocked,deferred,commentary_status}`。
- 只读账户 CLI 沿用 `python -m investment_assistant.views.account show`；综合核对走 `python -m investment_assistant.workflow check`（D45），不另造 PM 执行命令。

## 怎么验证一次

1. 获准且 031 就绪后运行 daily，核对真实账户与活跃模拟账户每 session 一行硬结果、账户隔离、order 与宿主重算一致。
2. 准备同组超限、同标的重复、单票 / 总敞口超限、现金不足、未归类案例；逐条看首 reason、硬拒绝与提示的区别。
3. 改模拟账户 sector_cap，下一交易日核对第八段证据与拒单；照计划执行账户行为不因组合检查改变。
4. 关闭后运行：skipped/feature_disabled、不执行硬检查、None 不拦、Today not_computed。开回再运行并使 PM 失败：保留硬结果、显示 degraded。确认开与关两条 markers、版本与参数分界。

## 已知限制

SIC 两位是粗分类，未归类不阻断不代表风险为零。贪心装入顺序不是收益预测，经理建议仍须人工判断。组合检查 / 页面通过不能替代前向账户证据；Gate HOLD 不变，验收见 [清单 §3](../roadmap/v2.12-v2.15-acceptance.md)。
