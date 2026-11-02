# 功能开关

> v2.12–v2.15，随 `v2.15.0` 一次发布（发布日期 TBD，使用验收待做）；依据 [合并 spec §9](../specs/2026-11-01-v2.12-v2.15-five-layers-and-agent-roles-design.md#9-功能开关)。

## 做什么

四项默认全开，没有变更行也视为开。它们是定位故障的熔断开关，每次翻动只追加 `feature_flag_changes`，保留之前值、之后值、理由、request_id 与时点。

| 开关 | 关闭效果 |
| --- | --- |
| `backup_sources` | 只用主源，`fallback.tried=false`，失败照实显示；开启也不会把未注册的 Stooq 变成可用源 |
| `company_researcher` | 跳过研究员，research.status=disabled，提议者仍是 agent-4；新研究请求 409 feature_disabled |
| `portfolio_manager` | **整个 portfolio 节点** skipped/feature_disabled，不执行硬检查；fact「本版未检查」、validate_orders(None) 不作组合拦截、accounts 照常、Today not_computed |
| `optimize_proposals` | propose 节点 skipped/feature_disabled；路线页、批准 / 驳回、brief 与导出仍可用 |

daily 新运行在 initial_state 读取并冻结 `DailyState.features`；中途翻动下次 daily 生效，续跑沿用原冻结值，`job_reports.summary.features` 留证。批外研究等入口读取当下值。开关状态与模型可用性不同：portfolio 开启、已产出硬结果后 PM 失败才是保硬结果的 degraded；关闭不是这种降级。

## 页面

「设置 → 功能开关」`#settings/features`：四行当前状态，enabled「已开启」、disabled「已关闭」，加真实历史 DataTable。开与关均必填理由（1–300 字），关闭需 ConfirmDialog；成功后重新 GET 读回 flags 和 history。无历史时显示「尚无变更记录」，不得用当前值拼成历史。

账户曲线记录 feature 分界；它只表示开关变更，不证明当日某条策略已经使用新值，具体运行仍看冻结 features。

## 接口与命令行

- `GET /api/settings/features → {flags:[{flag,enabled,changed_at,reason,history_count}],history:[{id,flag,enabled_before,enabled_after,reason,request_id,created_at}]}`；history 含全部真实变更，按 id 升序。
- `POST /api/settings/features/{flag}` body `{enabled,reason,request_id}` → `{flag,enabled,changed_at}`，request_id 保证回放；前端提交成功后读回。
- 运行冻结的开关值存在该次运行的 `job_reports.summary.features`（`{flag: bool}`，`workflow/graph.py` 在收尾写入；续跑沿用同一份）。可读的入口是 `GET /api/jobs/reports?task=daily&limit=50`：在返回的 `reports[].summary.features` 里按 `run_id` 找到该次运行。`tasks.daily show` 只打印节点表，运行详情（`/api/runs/{run_id}/detail`）也不展示冻结值；不新增开关 CLI；未知开关名返回 404 `unknown_flag`，迁移 031 未执行返回 503 `migration_031_missing`。

## 怎么验证一次

1. 获准且迁移 031 后，读取四项默认值与真实历史；开 / 关分别填理由，核对 POST 后 GET 出现新行，原行不改。
2. 在运行中翻动，核对本次仍用原冻结值、续跑沿用、下一次 daily 才改变；历史与运行摘要分开读取。
3. 每项单独关闭一次，核对上表语义；尤其组合关闭为 not_computed、不跑硬检查，和开启后 PM 失败的 degraded 分别验证。
4. 关闭 optimize_proposals，核对已有提案仍可决定、brief 可重试、导出可读；开启 / 关闭均在账户图及分界列表可追溯。

## 已知限制

开关不删除历史，不回算旧结果；不提供分阶段上线的特殊组合逻辑。关闭不是停掉全体模型或改账户策略，模型 token 仍共用当日预算。真实迁移、写入与使用验收尚未执行，详见 [验收清单](../roadmap/v2.12-v2.15-acceptance.md)。
