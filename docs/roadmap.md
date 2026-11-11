# Roadmap（v3）

v3 重做的目标（spec §2 目标 1–3）：入口是 agent——每天一份简报回答「最近行情适合交易什么」，落到个股；先出草稿（规则算出的个股名单，不调模型，事实任务一结束就存在），再自动取前 5 只做二次确认并输出交易计划，之后可以在同一个工作区对话追问。

| 步 | 内容 | 版本 | 状态 |
| --- | --- | --- | --- |
| 0 | Pi 可行性验证 | v3.0.0 | 已完成（2026-11-05 十项检查 10/10 通过，见 [operations/pi-unattended.md](operations/pi-unattended.md)） |
| 1 | 事实层与工具面 | v3.0.0 | 已完成（迁移 032 已在真实库执行；2026-11-06 真实库成功路径试跑与真实 Pi 冒烟通过，见 [operations/v3-facts.md](operations/v3-facts.md)） |
| 5 | 旧系统下线（提前） | v3.0.0 | 已完成（2026-11-06）：后端、前端、概念卡与文档移除或归档；迁移 033 已在真实库执行；旧服务已 `bootout`（见 [operations/migrations-and-backup.md](operations/migrations-and-backup.md)、[operations/local-service.md](operations/local-service.md)） |
| 2 | 每日简报 | v3.1.0 | 代码与测试已落地（`daily run` 五件事、错误日 / 重跑 / 补查、Discord 推送、LaunchAgent 模板）；**待上线**（装 LaunchAgent、连续 3 个定时交易日无人工干预，spec §15），见 [operations/daily-run.md](operations/daily-run.md) |
| 3 | 页面服务与对话 | v3.1.0 | 代码与测试已落地（外壳上的四块页面 + 设置面板、`/api/...` 接口、SSE 对话、运行时锁与设置）；**待上线**（重新加载页面服务），见 [operations/local-service.md](operations/local-service.md) |
| 4 | 概念学习 | — | 未开始 |
| 6 | 检验与复盘（v3.1 之外新增）：已结束计划的结果与按规则 / 形态 / 行业分组的胜率与盈亏比、未执行计划的假设结果、周报 | v3.2.0 | 代码与测试已落地（检验与复盘、研究与跟踪、页面的时间控件与金额）；2026-11-09 经用户授权上线前两步（备份与迁移 035、重建前端并重载页面服务，spec §13 第 1、2 步）；**待观察**（下一个定时交易日的研究复查与周五周报、对话里发起一份真实研究走一轮，spec §13 第 3、4 步，通过后发布 v3.2.0），v3.2.1 工作台壳与数据视图 2026-11-09 已发布（迁移 036、重建前端并重载服务、真实 Pi 冒烟通过），v3.2.2 页面卡片化与工具面板 2026-11-10 已发布（重建前端并重载服务、真实页面核对），见下方链接 |
| 7 | 账户维度：仓位上限与换仓比较（v3.1 / v3.2 之上新增）：总仓位上限 60%、钱不够时拿一只持仓与候选比较（继续持有 / 半仓换 / 全仓换，三道闸）、部分卖出、换仓复盘 | v3.3.0 | 代码与测试已落地（迁移 037、工具面 38 个、换仓记录与复盘、页面五处）；**2026-11-11 已上线**（spec §14 第 1–4 步：备份、迁移 037、重载页面服务、核对账户页）；**观察中**（第 5 步：第一次真实换仓比较，或连续 5 个定时交易日没有触发而简报正常；发布日期、tag 与 push 另行授权），见 [operations/migrations-and-backup.md](operations/migrations-and-backup.md#037-与-v33-上线五步第-1-4-步已执行第-5-步观察中)、[operations/local-service.md](operations/local-service.md#v33-上线五步第-1-4-步已执行第-5-步观察中) |

v3.1.0 在第 2、3 步之外新增三样 v3 设计里没有的东西（[v3.1 设计](specs/2026-11-06-v3.1-plan-line-account-and-web-design.md) §1）：**计划线**（一只股票一条跨天的线，状态七态，名额与替换）、**止损纪律**（买入即挂单、只上不下、移动止损、触及必须有交代）与**账户**（期初现金 + 出入金 + 成交算出现金、总资产与盈亏）。迁移 034 已在一次性库验证，2026-11-07 经用户授权在真实库执行；每日任务与页面服务同日上线，「连续 3 个定时交易日无人干预」待真实运行（spec §15 第 6 步）。

上线六步（每一步需用户单独授权）见 [v3.1 设计](specs/2026-11-06-v3.1-plan-line-account-and-web-design.md) §15；其中第 6 步（连续 3 个定时交易日无人工干预产出简报）完成后更新本文件与 CHANGELOG 并发布 v3.1.0。

- v3 设计：[spec](specs/2026-11-05-agent-first-rebuild-design.md)（步骤与变化见 §15.6）；第一阶段实施：[plan](plans/2026-11-05-v3-facts-and-tools.md)（第 0、1 步与文档归档）
- v2 提前移除：[spec](specs/2026-11-06-v2-removal-design.md)、[plan](plans/2026-11-06-v2-removal.md)（第 5 步）
- v3.1：[spec](specs/2026-11-06-v3.1-plan-line-account-and-web-design.md)、[plan](plans/2026-11-07-v3.1-plan-line-account-and-web.md)（第 2、3 步 + 计划线、止损纪律、账户）
- v3.2：[spec](specs/2026-11-08-v3.2-retrospect-research-and-page-polish-design.md)、[plan](plans/2026-11-08-v3.2-retrospect-research-and-page-polish.md)（第 6 步检验与复盘 + 研究与跟踪 + 页面的时间控件与金额）
- v3.2.1：[spec](specs/2026-11-09-v3.2.1-workbench-shell-and-data-views-design.md)、[plan](plans/2026-11-09-v3.2.1-workbench-shell-and-data-views.md)（工作台壳、纸面视觉与工具快照；[上线记录](operations/local-service.md#v321-上线四步2026-11-09-已执行)）
- v3.2.2：[spec](specs/2026-11-10-v3.2.2-page-cards-and-tool-list-design.md)、[plan](plans/2026-11-10-v3.2.2-page-cards-and-tool-list.md)（今日与账户页卡片化、工具面板；2026-11-10 已发布，执行记录见 [local-service.md](operations/local-service.md#v322-上线两步2026-11-10-已执行)）
- v3.3：[spec](specs/2026-11-11-v3.3-account-cap-and-rotation-design.md)、[plan](plans/2026-11-11-v3.3-account-cap-and-rotation.md)（第 7 步账户维度：仓位上限 60%、换仓比较、部分卖出、换仓复盘；迁移 037；上线五步见 spec §14，第 1–4 步 2026-11-11 已执行，第 5 步观察中）
- v2 的路线与验收记录（已归档，只读）：[roadmap](archive/v2/roadmap/roadmap.md)

**顺序为 0 → 1 → 5 → 2 → 3 → 6 → 4。** 第 1 步完成后用户决定以 v3 为主要迭代方向，接受第 2 步完成前没有每日推送和页面，所以第 5 步提前做，让第 2、3 步在只剩 v3 的代码上开工（依据与范围见 [v2 移除设计](specs/2026-11-06-v2-removal-design.md)）。

2026-11-08 用户决定 v3.2 以第 6 步「检验与复盘」为主线（第 4 步概念学习要先单独开一轮设计讨论，排在其后）；同时提出两项：页面的时间控件与金额显示（两位小数、美元）、由用户主动发起对某一标的的研究（留记录并每天跟踪；研究转成的计划不占待入场名额）。三样合为 v3.2.0，spec 与 plan 见上；代码与测试已落地，2026-11-09 已执行上线第 1、2 步（迁移 035、页面服务重载），第 3、4 步待真实运行，见 v3.2 spec §13。

2026-11-11 用户提出账户维度的缺口（持仓 A 在、候选 B 进来时要不要换），决定上限先取 60%、换仓每 5 个交易日最多一次、缺省继续持有；合为 v3.3.0，排在 v3.2 之后，不改第 6 步的观察期安排。代码与测试已落地，上线需用户单独授权，见 v3.3 spec §14。

本文件直接修改维护。
