# 系统内 roadmap、优化提案与实施 brief

> v2.15，随 `v2.15.0` 一次发布（发布日期 TBD，使用验收待做）。真实库导入、旧件冻结与导出替换尚未执行，需要单独授权。依据 [合并 spec](../specs/2026-11-01-v2.12-v2.15-five-layers-and-agent-roles-design.md) §8、§24。

## 做什么

目标、版本、决策、验收证据分别进 `roadmap_goals/roadmap_versions/roadmap_decisions/roadmap_evidence`，只追加、带 version，更正以 supersedes 指向旧行。版本状态为 planned / in_progress / engineering_done / user_acceptance_pending / done / dropped，工程完成与使用验收完成分开。

初始四份 `config/roadmap/*.yaml` 由人工整理：1 个目标、17 个版本（v1、v2.0–v2.15）、124 条连续决策、11 条来源验收摘要。后四版 planned，不伪造 tag、发布或验收；quote 只保留可核实逐字原话，摘要 / 作者代定保持空 quote。相同键同内容导入跳过，不同内容报错，不覆盖历史。

daily 的 propose 在 retro 后，提案日按运行 cutoff 的**北京日期是周六**判定（也就是纽约周五收盘后的那一次运行，夏令时与冬令时都一样；`optimize.evidence.is_proposal_day` 是唯一口径），其他运行为 skipped/not_proposal_day；手动 `tasks.daily run --force-propose` 可不等周六（开关关着时仍然跳过）。宿主先冻结五段证据包：

| 段 | 证据 |
| --- | --- |
| `data_failures` | 近 7 天各族 / error_kind 失败目标、备源使用、partial / failed |
| `agent_quality` | 近 7 天 mode / reason、研究 blocked、补查 deferred、后续成功修订代理统计 |
| `retro_patterns` | 近 30 天同 scope 至少 2 次的教训 / 草稿重复 |
| `route` | user_acceptance_pending 超过 30 天与近 3 月主指标流程复查信号；缺数据不是零 |
| `open_proposals` | 真实未决提案，防止标题重复 |

O2 的 `measurement=counter_followed_by_successful_revision` 指「运行级后续修订比例，不证明逐条反方意见被采纳或因果关系」，不是已证实反方采纳率。

`agent-optimize-1` 每次至多 3 条，来源 data / agent_quality / route，必须引用包内真实 `fact:<key>#<id>`；无证据不调模型，success/no_evidence；不存在的 ref 导致该条丢弃并记 rejected_refs。包保存在生成运行的 `agent_runs.output.evidence_pack`，之后读取不能用今日 SQL 冒充当时证据。

**批准前不改路线或代码**：proposed 提案在 roadmap_versions / roadmap_briefs 中不得有引用；核对违规集合是全部 proposed ids 减去 clean ids，空集合才通过。批准需理由和 version_id，同事务新增 planned 版本与决策，然后提交后台 `agent-impl-brief-1`；驳回只改提案状态和理由。brief 给人写背景证据、改什么 / 不改什么、模块与 prompt 建议、验收和风险，不自动实施。关闭 optimize_proposals 不影响决定、brief、导出。

## 页面

`#roadmap` 有路线 / 提案 / 决策 / 证据四页签。路线看目标、主指标、版本定义与退出条件；提案展开冻结证据行，批准 / 驳回先确认和填理由，批准默认建议下一个未用 v2.x；决策显示编号、日期、问题、决定与原话；证据按版本分组。右上导出 Markdown。

版本 / 决策详情展示真实全部 revision（按 version/id 升序，含当前行），不拿当前行充历史；404 显示错误。brief 共用 not_requested / pending / ok / error：批准或 retry 返回 pending 只说明任务提交，继续读回详情，pending 可「刷新 brief」，error 可重试；只有读回 ok 才展示成功正文。历史证据包缺记录时显示「历史证据包缺少该引用的记录」，不补造证据。

## 接口与命令行

| 接口 | 冻结形状 |
| --- | --- |
| `GET /api/roadmap` | `{goals,versions,decisions,evidence,exported_at}`，各取最新 revision |
| `GET /api/roadmap/versions/{version_id}` | `{version,history,brief,brief_status}`；无批准关联 / brief 为 not_requested，有批准关联但无 brief 行为 pending，已有行取最新 ok/error |
| `GET /api/roadmap/decisions/{no}` | `{decision,history}`，不存在返回 404 |
| `GET /api/roadmap/proposals?status=` | `{items:[...]}`，每项带 `evidence_rows:[{ref,body}]`，body 为冻结包原行；旧包缺失为空列表 |
| `POST /api/roadmap/proposals/{id}/decide` | `{decision,version_id?,reason,request_id}` → `{status,version_id,brief_status}`；rejected 后两项为 null |
| `POST /api/roadmap/briefs/{version_id}/retry` | `{version_id,brief_status:'pending',run_id}`，run_id 沿用 core.runner；随后 GET 详情 |
| `GET /api/roadmap/export.md` | `text/markdown`，与 CLI 共用导出函数 |

命令行（`import` 写真实库，`export` 只读；均需 031 就绪，`--help` 不碰库）：

```bash
python -m investment_assistant.optimize.roadmap import --dir config/roadmap
python -m investment_assistant.optimize.roadmap export [--out docs/roadmap/roadmap.md]
python -m investment_assistant.workflow check --session YYYY-MM-DD
python -m investment_assistant.tasks.daily run --manual --force-propose
```

按 D45，原先设想的 `optimize … check` 并入 `workflow check`（不另设子命令）：`--session` 汇总该交易日全部核对项（含「批准前不变」），`--since` 保留旧的计划与推导逐份核对。真实导入、旧件冻结为 `docs/roadmap/roadmap-frozen-2026-11-01.md` 与导出替换只能在获准后按此顺序做，当前都没有执行。

## 怎么验证一次

1. 获准后导入种子，核对 17 版本、#1–#124、逐项原话保真；重复同内容不新增，同键不同内容报错。页面与导出条数一致。
2. 周五 session（如 2026-11-06，对应北京周六）或手动 force，核对五段证据、至多 3 条、生成包与列表 evidence_rows 一致；之后新增数据不能改写历史包。
3. 决定前运行综合 check 确认没有版本 / brief 引用；批准一条产生 planned 版本与决策，驳回一条只改状态。批准不表示实施或验收通过。
4. 人为失败的 brief 读回 error，retry 返回 pending，再 GET 到真实 ok/error；核对全 revision 含当前，404 不补造。关闭优化开关后仍可读、决定、重试和导出。
5. 用 brief 在仓库按 spec / plan 开工，完成后回填真实验收证据；这一步是使用验收，尚未发生。

## 已知限制

系统只提案，人实施；主指标继续 monthly-5 的账本优先、精确端点缺失回退，不改阈值或策略。初始导入不是从 Markdown 自动解析。当前仓库 roadmap 仍为受限维护源，真实导入并发布后才按「系统内记录为准、仓库为导出件」运行；全部策略 / 指标 Gate HOLD 不变。
