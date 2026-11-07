# 每日任务（v3.1）

一个 LaunchAgent 在北京时间 11:30（周二至周六，美股收盘后的早晨）触发一条命令 `python -m investment_assistant.daily run`。
本文是它的运维说明：它做什么、怎么装、重跑与补查的行为、简报落在哪、预算与重试、故障排查；末尾是本版验收条款与测试的对照表。

## 一天做五件事（spec §5）

1. **采集**：沿用 `facts` 的八族采集；采集目标在原有持仓之外新增待入场计划的股票，保证它们掉出股票池也有当天的日线。
2. **事实计算**：沿用现有的名单计算，结果不变。
3. **计划与持仓复查**（宿主计算，不调模型）：每份有效计划当天的状态变化、每只持仓下一个交易日的操作、名单的连续性标记。
4. **发布草稿**：草稿已含第 3 步全部结果，写库（`v3_briefs`，每个交易日一行）并写文件。
5. **简报会话**（agent）：按当天待办逐项给结论；完成后合成最终简报，写库、写文件、发 Discord。

第 5 步失败或超时不影响前四步的产物：最终简报里宿主算出的内容照常，agent 的部分写「未完成」和原因。
数据库不可达或基准 SPY 当期日线缺失时写一份只有原因的错误简报并推送一次通知；此时若有持仓，第一行是「今天无法检查止损」加原因。
没有选定模型（设置三项为空）时前四步照常，不启动会话，简报保持草稿、页脚写「未选定模型，二次确认未做」。

## 命令行与退出码

```bash
.venv/bin/python -m investment_assistant.daily run                 # 跑一次（默认先采集）
.venv/bin/python -m investment_assistant.daily run --no-collect   # 跳过采集
.venv/bin/python -m investment_assistant.daily run \
  --runtime pi --provider <提供方> --model <模型 id>               # 单次临时指定（三项必须同给）
.venv/bin/python -m investment_assistant.daily settings show      # 当前设置
.venv/bin/python -m investment_assistant.daily settings options   # 可选的运行时、提供方与模型
.venv/bin/python -m investment_assistant.daily settings set \
  --runtime pi --provider <提供方> --model <模型 id>               # 保存设置（保存前检查可用性）
```

`run` 的 stdout 是一行 JSON（`session`、`status`、`attempts`、`complete`、`pushed`、`brief_path`）。退出码：

| 退出码 | 含义 |
| --- | --- |
| 0 | 成功；`status` 为 `final`（完整简报）或 `draft`（未选定模型的正常产物） |
| 1 | 事实任务错误（错误简报已写库并推送） |
| 2 | 参数错误或数据库不可用（stderr 有原因） |
| 3 | 已有运行时会话在跑（锁被占，见「故障排查」） |

## 专用 Pi 目录与设置命令

简报会话用独立的 Pi 配置目录 `~/.pi-ia`（环境变量 `INVESTMENT_ASSISTANT_PI_DIR` 覆盖；与 [pi-unattended.md](pi-unattended.md) 的实测做法一致），
与日常使用的 Pi 配置完全隔离。提供方与模型 id 只存在库里（`v3_settings`）或以命令行参数临时给出，不进任何仓库文件。

- `settings options` 从该目录的 `models.json` 与 `auth.json` 读出可选的提供方与每个提供方的模型 id；
- `settings set` 保存前用 `pi auth check` 检查提供方可用，不可用给出原因字符串并拒绝保存；
- 保存后页面的设置面板显示同一份设置，两边改的是同一行。

## LaunchAgent 的安装

模板在 `scripts/launchd/local.investment-assistant.daily.plist.example`，安装三步（本 plan 只列步骤，安装需用户授权）：

```bash
# 1. 复制模板
cp scripts/launchd/local.investment-assistant.daily.plist.example \
   ~/Library/LaunchAgents/local.investment-assistant.daily.plist

# 2. 把文件里的 __REPO__ 与 __HOME__ 两个占位符替换成本机路径
#    （__REPO__ = 仓库根目录，__HOME__ = 用户主目录）

# 3. 加载（launchd 会按 StartCalendarInterval 在周二至周六 11:30 触发）
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.investment-assistant.daily.plist
```

卸载：`launchctl bootout gui/$(id -u)/local.investment-assistant.daily`。模板里设了 `INVESTMENT_ASSISTANT_RUNTIME_BASE`（简报与草稿目录的上级）
和 `PI_CODING_AGENT_DIR`（专用 Pi 目录）；日志在 `~/Library/Application Support/investment-assistant/logs/daily.stdout.log` 与 `daily.stderr.log`。
页面服务的重新加载见 [local-service.md](local-service.md)。

## 同一交易日重跑与漏跑补查

- **重跑幂等**：宿主产生的变化记录按（计划、交易日、类型）唯一，同一天重跑不产生重复行；简报行按交易日 upsert，更新为最新一份；该交易日已推送过成功简报就不再推送。
- **先错后成再推一次**：先推送过「没法算」的通知、之后同一天重跑成功的，会再推送一次成功的简报；再重跑不再推送。
- **漏跑补查**：每份有效计划记着自己复查到了哪个交易日（`reviewed_through`）。每日任务从那之后的每个有日线的交易日按顺序逐日复查到当天 S，不只看 S 这一天——机器关机一天，那天盘中触及止损也会被补查出来，记录的日期写它实际发生的交易日。补查出来的变化照常各记一行。
- 缺日线的交易日不推进 `reviewed_through`：日线补上后，「收盘跌破止损」还能对那一天判定。

## 简报与草稿文件在哪

目录按优先级：配置键 `v3.briefs_dir` → 环境变量 `INVESTMENT_ASSISTANT_RUNTIME_BASE` 下的 `briefs/` → 都没设时仓库本地 `.local/v3/briefs/`（与 [v3-facts.md](v3-facts.md) 的草稿目录一致）。

- `<交易日>.draft.md`：第 4 步的草稿（十段简报去掉 agent 部分，开头写「二次确认未完成」）；
- `<交易日>.md`：最终简报（含 agent 结论与页脚的运行时、模型、token 用量与耗时）；
- `briefs/<交易日>/attempt-N/`：简报会话第 N 次尝试的工作目录（每次尝试新进程、不续接会话）。

数据库 `v3_briefs` 里每个交易日一行，含草稿与最终稿、状态（`draft` / `final` / `error`）、所用模型、用量与是否已推送。

## Discord 通道

- 简报摘要发 **signals** 频道；地址取配置 `notify.webhooks.signals`，缺省环境变量 `DISCORD_WEBHOOK_SIGNALS`。
- 每个交易日推送一次：顺序是今日操作（有就排第一行）、一句话、持仓操作各一行、新计划与结束的计划各一行、文件位置。
- 按长度上限分条（每条不超过 1900 字符，超长单行按字符切），由构造保证不超限，不会因超长发不出去。

## 预算与重试

- 简报会话的总预算取配置 `v3.brief_budget_minutes`（缺省 20 分钟），**含重试**；每次尝试的时限是剩余预算。
- 完成标准是当天待办全部有结论；没完成且预算有剩就再启动一次，**只处理剩下的**，已保存的结论不重做。
- 尝试之间指数退避：0.5 秒起、每次翻倍、封顶 30 秒，避免「每次尝试立即报错」在预算里热转。
- 运行时自身起不来（进程无法启动、传输中断）不重试，记下原因收工；单次尝试无输出被超时杀掉记 `timeout`。
- 超预算还没完成的待办在最终简报里逐项写「未完成」和原因；持仓的操作不受影响，它由宿主算。
- token 用量与耗时按所有尝试累加，记进简报页脚。

## 故障排查

- **锁文件**：每日任务与页面的每轮对话共用一个运行时锁 `pi.lock`（在 `INVESTMENT_ASSISTANT_RUNTIME_BASE` 下，缺省 `.local/v3/pi.lock`），
  同一时刻只允许一个 Pi 进程。锁被占时 `daily run` 退出码 3，页面提问得到 409。锁用 `flock` 实现，持有进程退出即自动释放；
  怀疑卡死时看锁文件内容（`brief` 或 `chat`，说明谁持有），不要手工删文件。
- **`stopReason`**：Pi 每轮结束的事件里 `stopReason == "error"`（或 `errorMessage`）时该次尝试记失败，简报行的未完成原因写它；
  最后一条助手消息正常结束（`stop`）而待办未完成时，按预算重试。
- **一份计划的复查失败**：每份计划一个事务，毒数据只回滚它自己，`facts run` 的 stdout JSON 里 `review.errors` 列出该计划与原因，其余计划照常。
- **简报一直停在草稿**：通常是没选模型（`settings show` 为 null）或每次尝试都失败；看 `daily.stderr.log` 与简报行的未完成原因。
- **错误简报（只有原因）**：数据库不可达是退出码 2；基准日线缺失是退出码 1、运行记录 `status=error`，多为数据源当天还没有收盘数据，稍后重跑。

## 验收对照

下表是 v3.1 spec（[2026-11-06 v3.1 设计](../specs/2026-11-06-v3.1-plan-line-account-and-web-design.md)）§18 的 A1–A22 与沿用的 v3 设计验收条款，
逐条写下「由哪个测试证明」；测试名都实存于仓库，`tests/` 下的为后端、`web/src/` 下的为前端。没有任何测试能证明的（只能真实运行才能证明的）标「待真实运行」。

### 本版新增（A1–A22）

| # | 标准 | 证明 |
| --- | --- | --- |
| A1 | 同一股票连续两天上榜不产生第二份计划；直接保存被拒绝并返回已有的那份 | `tests/test_v3_tools_plan.py::test_second_day_save_plan_hits_active_plan_exists_and_continue`、`tests/test_migration_034.py::test_one_active_plan_per_ticker_is_a_db_constraint` |
| A2 | 保存计划的各条校验各有拒绝测试（名单外、无确认、价位、股数、入场类型、已有计划、名额、替换名次、同日二次替换） | `tests/test_v3_tools_plan.py::test_save_plan_rejections`、`test_save_plan_rejected_when_slots_full`、`test_save_plan_rejected_when_replacement_rank_not_better`、`test_save_plan_replacement_marks_victim_then_rejects_second` |
| A3 | 待入场三种结束各有带原因的记录；agent 复查否决作废 | `tests/test_v3_review.py::test_pending_three_endings_leave_reason_events`、`tests/test_v3_tools_plan.py::test_save_verdict_veto_invalidates_pending_plan` |
| A4 | 止损只不上不下：移动止损 / 对话调整 / 修订三条途径 | `tests/test_plan_line.py::test_target_hit_then_trailing_raises_never_lowers`、`tests/test_v3_tools_chat.py::test_raise_stop_only_upward_and_below_close`、`tests/test_v3_tools_plan.py::test_revise_plan_only_pending_and_records_changed_keys`（open 计划拒绝修订） |
| A5 | 当天待办每项有结论或明确的未完成原因 | `tests/test_v3_agenda.py::test_done_flags_and_complete`、`tests/test_daily_session.py::test_budget_exhaustion_reports_incomplete` |
| A6 | 止损触及未见卖出：简报第一段、Discord 第一行、页面今日操作；卖完消失 | `tests/test_v3_brief.py::test_today_actions_section_first_or_absent`、`test_discord_lines_order`、`web/src/lib/home/TodayActions.test.ts`、`tests/test_v3_review.py::test_stop_hit_persists_until_sold`、`tests/test_v3_tools_chat.py::test_sell_all_closes_plan_and_clears_actions` |
| A7 | 持仓当天缺日线写明无法检查止损 | `tests/test_v3_review.py::test_missing_bar_marks_holding_unchecked`、`tests/test_plan_line.py::test_missing_bar_marks_holding_unchecked` |
| A8 | 移动止损只上不下、不低于成本（补录例外不高于现价） | `tests/test_plan_line.py::test_target_hit_then_trailing_raises_never_lowers`、`test_trailing_stop_formula`、`test_trailing_stop_ignores_cost_floor_above_price`、`tests/test_v3_review.py::test_split_folds_trailing_window_no_false_stop_hit` |
| A9 | 账户与手算一致；期初前成交不入现金；股数按三级本金；无资金照常保存 | `tests/test_account.py::test_ledger_matches_hand_calculation`、`test_trades_before_opening_do_not_touch_cash`、`test_sizing_snapshot_precedence`、`tests/test_v3_tools_plan.py::test_plan_without_equity_and_without_room`、`tests/test_v3_tools_ledger.py::test_sizing_takes_ledger_equity_after_opening` |
| A10 | 买入返回止损单指令；未确认提示；上调后需再确认 | `tests/test_v3_tools_chat.py::test_buy_with_pending_plan_returns_stop_order_and_flow`、`tests/test_v3_view.py::test_today_actions_follow_section_7_4_order`、`tests/test_plan_line.py::test_next_action_priority`、`tests/test_api_plans.py::test_stop_order_previews_then_takes_effect` |
| A11 | 无计划买入补录；缺日线止损暂缺，补上后算出 | `tests/test_v3_tools_chat.py::test_buy_without_plan_creates_adopted_plan`、`tests/test_v3_review.py::test_adopted_plan_gets_levels_when_bars_arrive` |
| A12 | 会话被杀后重试只处理剩下的，已有结论不变 | `tests/test_daily_session.py::test_partial_failure_retries_only_the_remaining_items`、`test_killed_session_keeps_draft_and_records_reason` |
| A13 | 到期与论点失效各自产生「下次开盘卖出」并持续到卖出 | `tests/test_plan_line.py::test_max_hold_sets_sell_instruction_once`、`tests/test_v3_tools_plan.py::test_save_holding_review_broken_sets_sell_instruction`、`tests/test_v3_tools_chat.py::test_sell_all_closes_plan_and_clears_actions` |
| A14 | 平仓结果与手算一致 | `tests/test_plan_line.py::test_close_outcome`、`tests/test_v3_review.py::test_split_adjusts_initial_stop_for_r_multiple`、`tests/test_v3_tools_chat.py::test_sell_all_closes_plan_and_clears_actions` |
| A15 | 名单连续性；无变化日「没有变化」 | `tests/test_v3_continuity.py::test_two_days_new_streak_and_dropped_reasons`、`test_streak_carries_previous_count`、`test_first_run_without_previous`、`tests/test_v3_brief.py::test_no_changes_says_so` |
| A16 | 同日重跑不重复记录、不重复推送 | `tests/test_v3_review.py::test_rerun_same_session_no_duplicate_events`、`tests/test_migration_034.py::test_host_event_once_per_session_and_type`、`tests/test_daily_job.py::test_same_session_rerun_does_not_repush` |
| A17 | 模型不可用的日子草稿齐全 | `tests/test_daily_job.py::test_no_model_keeps_draft_note_and_skips_push`、`test_runtime_error_final_brief_keeps_host_parts`、`tests/test_daily_session.py::test_killed_session_keeps_draft_and_records_reason` |
| A18 | 页面计划卡片、记成交表单预填、确认挂单 | `web/src/lib/home/PlanCard.test.ts`、`web/src/lib/home/TradeForm.test.ts`、`web/src/lib/home/TodayActions.test.ts`、`tests/test_api_plans.py::test_plan_detail_card_and_events` |
| A19 | 对话写入先复述后确认；作废后重算 | `tests/test_v3_tools_chat.py::test_chat_writes_preview_before_confirming`、`test_record_cash_flow_kinds_and_opening_once`、`test_record_equity_returns_reconciliation`、`test_cancel_plan_only_pending_and_idempotent`、`test_watchlist_add_and_remove`、`test_void_buy_that_breaks_fifo_is_rejected`、`test_void_buy_cancels_plan_and_restores_cash` |
| A20 | 拆股按比例调整并留记录 | `tests/test_plan_line.py::test_split_adjust`、`tests/test_v3_review.py::test_split_adjusts_levels_with_event` |
| A21 | 漏跑一天后补查出那天的止损触及 | `tests/test_v3_review.py::test_catch_up_finds_stop_hit_on_missed_session` |
| A22 | 先错后成再推一次，再重跑不推 | `tests/test_daily_job.py::test_error_day_pushes_once_with_stop_warning_line`、`test_error_then_success_pushes_again_then_stops` |

### 沿用的 v3 设计验收（编号是 v3 设计的）

| # | 标准 | 证明 |
| --- | --- | --- |
| 2 | `pi` 不可用时草稿照常、简报写明未完成、Discord 照常 | `tests/test_daily_session.py::test_pi_unavailable_records_reason_without_raising`、`tests/test_daily_job.py::test_runtime_error_final_brief_keeps_host_parts`、`test_error_day_pushes_once_with_stop_warning_line` |
| 6 | 缺日线不显示成「降级」 | `tests/test_v3_review.py::test_missing_bar_marks_holding_unchecked`、`tests/test_v3_brief.py::test_no_downgrade_word`、`tests/test_agent_workspace.py::test_workspace_never_says_degradation` |
| 7 | Discord 长度边界 | `tests/test_notify_discord.py::test_chunk_splits_by_lines_and_chars`、`test_send_text_posts_each_chunk` |
| 8 | 会话里只有本服务的工具 | `tests/test_runtime_pi.py::test_command_line_and_session_arguments`（`--tools mcp__ia__*`）、`test_env_whitelist_passes_proxy_and_pi_vars_only` |
| 11 | 连续 3 个定时交易日无人干预 | **待真实运行**（LaunchAgent 未安装，spec §15 第 6 步） |
| 12 | 切换模型后简报页脚如实显示、旧对话只读 | 设置读写：`tests/test_api_settings.py::test_put_saves_and_reads_back`、`tests/test_api_chat.py::test_switching_runtime_marks_old_chats_read_only`、`tests/test_v3_brief.py::test_footer_has_disclaimer_and_model`；**真实切换待真实运行** |
| 13 | 页面读简报 | `tests/test_api_briefs.py::test_latest_brief_prefers_final_markdown`、`test_brief_by_session_draft_is_unfinished`、`web/src/routes/Home.test.ts`（简报块与历史切换） |
| 14 | 流式对话 | `tests/test_api_chat.py::test_turn_streams_and_second_turn_sees_the_first`、`web/src/lib/home/sse.test.ts`、`web/src/lib/home/ChatPanel.test.ts` |
| 15 | 隔离（专用 Pi 目录、自带工具禁用、环境白名单） | `tests/test_runtime_pi.py::test_command_line_and_session_arguments`、`test_env_whitelist_passes_proxy_and_pi_vars_only` |
| 16 | 简报运行期间提问得到提示 | `tests/test_api_chat.py::test_brief_running_is_409`、`web/src/lib/home/ChatPanel.test.ts`（409 显示「正在生成简报，请稍后再问」） |
| 17 | 换模型（设置面板） | `tests/test_api_settings.py::test_put_saves_and_reads_back`、`test_put_rejects_unavailable_and_keeps_store`、`test_options_lists_runtimes_providers_models`（面板调用的就是这三个接口；面板 UI 本身无专门前端测试） |
| 18 | 假实现（无 Pi、无网络跑全套） | `tests/test_runtime_fake.py`（全文件）、`tests/test_daily_session.py`（全部用假运行时） |
| 19 | 超时与取消 | `tests/test_api_chat.py::test_cancel_ends_stream_and_next_turn_works`、`tests/test_runtime_chat.py::test_timeout_marks_the_message_timeout`、`tests/test_runtime_pi.py::test_stalled_stream_times_out_and_kills` |

另：真实 Pi 客户端的冒烟（v3 设计验收的做法）**待真实运行**——本版没有再跑；上一次是 v3.0 在 2026-11-06 做的（见 [v3-facts.md](v3-facts.md) 冒烟记录）。
