# 每日任务（v3.1；v3.2 增研究复查与周报；v3.3 增换仓比较）

一个 LaunchAgent 在北京时间 11:30（周二至周六，美股收盘后的早晨）触发一条命令 `python -m investment_assistant.daily run`。
本文是它的运维说明：它做什么、怎么装、重跑与补查的行为、简报落在哪、预算与重试、故障排查；末尾是本版验收条款与测试的对照表。

## 一天做五件事（spec §5；v3.2 第 3 步加研究复查、周末日加周报）

1. **采集**：沿用 `facts` 的八族采集；采集目标在原有持仓之外新增待入场计划的股票与观察中研究的股票，保证它们掉出股票池也有当天的日线。
2. **事实计算**：沿用现有的名单计算，结果不变。
3. **计划复查与研究复查**（宿主计算，不调模型）：每份有效计划当天的状态变化、每只持仓下一个交易日的操作、名单的连续性标记；之后逐份复查观察中且有条件的研究（缺日线不判不推进、条件到了记一次待复看、到期无触发过期，与计划线同样的补查做法）。
4. **发布草稿**：草稿已含第 3 步全部结果，写库（`v3_briefs`，每个交易日一行）并写文件。
5. **简报会话**（agent）：按当天待办逐项给结论；完成后合成最终简报，写库、写文件、发 Discord。每周最后一个交易日的运行在最终简报之后另算一份周报（宿主算，见下文「周报」）。

第 5 步失败或超时不影响前四步的产物：最终简报里宿主算出的内容照常，agent 的部分写「未完成」和原因。
数据库不可达或基准 SPY 当期日线缺失时写一份只有原因的错误简报并推送一次通知；此时若有持仓，第一行是「今天无法检查止损」加原因。错误日不产出周报、下次运行也不补（页面的复盘块是现算的，那一周的汇总照常能看）。
没有选定模型（设置三项为空）时前四步照常，不启动会话，简报保持草稿、页脚写「未选定模型，二次确认未做」；周报照常产出并推送（它全是宿主算的）。

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

v3.2.1 的下次运行推算读取配置 JSON 的 `v3.daily_weekdays`（缺省 `[2, 3, 4, 5, 6]`，ISO 星期 1 为周一）、
`v3.daily_hour`（11）、`v3.daily_minute`（30），按 `Asia/Shanghai` 找严格晚于现在的下一个时刻。
这些配置仅用于推算，不改变 launchd 的触发时间；修改时间时须同步修改配置和已安装 plist 的 `StartCalendarInterval`，
否则页面显示的下次运行会与实际触发不一致。星期须为 1–7 的非空整数子集，小时 0–23、分钟 0–59。

模板在 `scripts/launchd/local.investment-assistant.daily.plist.example`，安装三步（本机已于 2026-11-07 经用户授权安装）：

```bash
# 1. 复制模板
cp scripts/launchd/local.investment-assistant.daily.plist.example \
   ~/Library/LaunchAgents/local.investment-assistant.daily.plist

# 2. 把文件里的 __REPO__ 与 __HOME__ 两个占位符替换成本机路径
#    （__REPO__ = 仓库根目录，__HOME__ = 用户主目录）

# 3. 加载（launchd 会按 StartCalendarInterval 在周二至周六 11:30 触发）
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.investment-assistant.daily.plist
```

卸载：`launchctl bootout gui/$(id -u)/local.investment-assistant.daily`。模板里设了 `INVESTMENT_ASSISTANT_RUNTIME_BASE`（简报与草稿目录的上级，`~/Library/Application Support/investment-assistant`）
和 `PI_CODING_AGENT_DIR`（专用 Pi 目录）。launchd 的 `EnvironmentVariables` 优先于 `.env`，所以这个值**必须与页面服务 `.env` 里的取值相同**：
两边共用其下的 `pi.lock` 做单进程互斥，不同就会各拿一把锁、简报会话与页面对话可能同时起 Pi 进程，简报文件也会分散在两个目录。日志在 `~/Library/Application Support/investment-assistant/logs/daily.stdout.log` 与 `daily.stderr.log`。
页面服务的重新加载见 [local-service.md](local-service.md)。

## 页面「定时任务」（v3.2.1，2026-11-09 已部署）

左栏 `#jobs` 显示周几、时间、`Asia/Shanghai` 时区与下次运行，以及缺省 20 行的事实运行 / 简报状态、推送与错误；展开行可看用量、耗时与模型。「打开当天简报」跳 `#today?session=YYYY-MM-DD`。顶栏的定时任务侧览只列最近 4 次、下次运行、观察中研究的条件与本周周报窗口；它与完整视图共用 `/api/runs` 缓存，不触发任务。交易日按纽约日历，页面时间按北京显示。

修改 `daily_weekdays` / `daily_hour` / `daily_minute` 后，核对已安装 plist 的 `StartCalendarInterval`，并按服务变更流程授权重载对应 LaunchAgent；页面的 `schedule.next_at` 只证明配置推算，不能证明 launchd 已同步。

## 同一交易日重跑与漏跑补查

- **重跑幂等**：宿主产生的变化记录按（计划、交易日、类型）唯一，同一天重跑不产生重复行；简报行按交易日 upsert，更新为最新一份；该交易日已推送过成功简报就不再推送。
- **先错后成再推一次**：先推送过「没法算」的通知、之后同一天重跑成功的，会再推送一次成功的简报；再重跑不再推送。
- **漏跑补查**：每份有效计划记着自己复查到了哪个交易日（`reviewed_through`）。每日任务从那之后的每个有日线的交易日按顺序逐日复查到当天 S，不只看 S 这一天——机器关机一天，那天盘中触及止损也会被补查出来，记录的日期写它实际发生的交易日。补查出来的变化照常各记一行。
- 缺日线的交易日不推进 `reviewed_through`：日线补上后，「收盘跌破止损」还能对那一天判定。

## 简报与草稿文件在哪

目录按优先级：配置键 `v3.briefs_dir` → 环境变量 `INVESTMENT_ASSISTANT_RUNTIME_BASE` 下的 `briefs/` → 都没设时仓库本地 `.local/v3/briefs/`（与 [v3-facts.md](v3-facts.md) 的草稿目录一致）。

- `<交易日>.draft.md`：第 4 步的草稿（十一段简报去掉 agent 部分，开头写「二次确认未完成」；v3.2 起「待入场计划」之后多一段「研究」）；
- `<交易日>.md`：最终简报（含 agent 结论与页脚的运行时、模型、token 用量与耗时）；
- `briefs/<交易日>/attempt-N/`：简报会话第 N 次尝试的工作目录（每次尝试新进程、不续接会话）。

数据库 `v3_briefs` 里每个交易日一行，含草稿与最终稿、状态（`draft` / `final` / `error`）、所用模型、用量与是否已推送。

## Discord 通道

- 简报摘要发 **signals** 频道；地址取配置 `notify.webhooks.signals`，缺省环境变量 `DISCORD_WEBHOOK_SIGNALS`。
- 每个交易日推送一次：顺序是今日操作（有就排第一行）、一句话、持仓操作各一行、新计划与结束的计划各一行、文件位置。
- 按长度上限分条（每条不超过 1900 字符，超长单行按字符切），由构造保证不超限，不会因超长发不出去。
- v3.2：研究条件到了的在「新计划」之后加一行「研究 {股票}：条件已到（{条件}）」。

## 周报（v3.2）

- **什么时候产出**：每周最后一个交易日（判定：当天的下一个交易日落在另一个 ISO 周）的运行，在最终简报之后算本周汇总，写进当天简报行的新列 `weekly_md`，并单独推一条 Discord（`pushed_weekly_at` 记推送时刻；同一周重跑不重推，与简报推送的幂等规则一致）。
- **没有选定模型的日子**：周报照常产出并推送（周报全是宿主算的，不依赖会话）；事实任务失败（错误简报）的日子不产出、下次运行也不补——页面的复盘块是现算的，那一周的汇总照常能看。
- **内容**：本周结束的计划几份、照做 / 有偏差各几份、偏差标签各出现几次；已平仓计划的 R 合计与各份一行；没买的计划各一份一行（结束原因、假设结果）；账户的周初与周末总资产、本周已实现盈亏；本周名单的新上榜、掉出、再次上榜最多的股票；页脚沿用简报（数据截止、规则未经验证、金额单位美元）。
- 页面复盘块顶部显示最近一周，可切换以前的周；周报的数字与页面复盘块出自同一个函数，只有一份口径。

## 换仓比较（v3.3）

v3.3 在当天待办里多一种判断（[spec](../specs/2026-11-11-v3.3-account-cap-and-rotation-design.md) §5–§7；代码已落地，真实库迁移 037 未执行前不会出现，见 [migrations-and-backup.md](migrations-and-backup.md#037-与-v33-上线五步未执行)）：对通过二次确认的候选 B，宿主先用规则价位判「钱够不够」，**钱够照旧出计划，和持仓无关**；钱不够才轮到换仓比较。全部由宿主在 `get_agenda` 里现算，agent 不自己决定。

- **待办里的标记**：每个候选确认项带 `rotation`：`short`（是否钱不够）、`compare`（是否需要比较）、`blocked` 与 `blocked_text`（不比较的原因）。需要比较的项，完成判定是「B 被否决或无法判断」「换仓结论为继续持有」「换仓结论为半仓或全仓且 B 的计划已保存」三者之一；不比较的算作已完成，agent 不必为它做二次确认。顶层的 `rotation` 给出 `compared_today`（当天简报是否已存过一行换仓记录）与 `last_session`（冷却期内最近一次半仓或全仓的交易日）。
- **什么时候不比较**（名单里写明原因，计划不出）：`no_holding` 仓位已到上限，没有可比的持仓；`compared_today` 当天已比较过一次（以存下一行记录为准，三种结论都算；B 被否决或无法判断没有记录，不算）；`cooldown` 冷却期内（上次换仓 MM-DD；R 到当天两端都算，不超过 `v3.rotation_cooldown_sessions` 个交易日，缺省 5，所以周一换仓、下周一可以再比）；`incomplete` 有持仓缺日线，仓位占比偏低。「继续持有」不进冷却；对话里用户发起的比较不受后两种限制，但复述里要说出最近换仓次数，结论同样计入冷却。
- **可比持仓**：已买入且当天止损没有触及、没有任何卖出指令（含尚未执行完的换仓）、有当天日线、当天持仓复查不是「不成立」；被选为 A 的持仓必须已有当天的复查结论，所以持仓复查排在待办最前。持有天数不设下限，补录持仓可比。
- **agent 的步骤**：先做 B 的二次确认 → 结论是确认且标明需要比较时调 `get_rotation_table` → 对要换掉的那只持仓读一次它自己的数据 → `save_rotation`（半仓或全仓在同一次调用里带 B 的论点与作废条件）。缺省是继续持有：半仓或全仓必须写出 A 哪里没按预期走并回应手续费那笔账，入参里没有任何数字，卖出股数与 B 的股数由宿主算。
- **结论落库**：继续持有写一行换仓记录，B 之后的 `save_plan` 被拒绝（`no_room: 仓位已到上限，比较后继续持有`）。半仓或全仓在一个事务里写换仓记录、B 的待入场计划（带「资金来自换仓」）与 A 的换仓卖出指令（「下次开盘卖出 N 股，换至 B」）；中途失败三者都不留，重试重新做这一步，不存在「A 有指令而 B 没计划」的状态。最终简报里没做完的写「换仓比较未完成」加原因，A 不产生任何指令。
- **换仓指令之后**：止损触及、持有到期、论点失效都高于换仓指令，A 之后出现其中任何一种就按那一种处理、换仓指令被取代并记一行；B 的计划没买就结束（过期、作废、被替换、取消）而 A 还没卖完时，每日复查末尾自动撤回换仓指令并记一行；用户也可以在对话或页面撤回（不执行换仓不算偏差）。卖出成交推进指令，卖完指令消失、计划平仓，离场原因记「换仓」（半仓换卖掉一半后剩余部分后来止损的，离场原因记止损）。没卖完的卖出之后，剩余持仓的止损价不变，「今日操作」显示「止损单数量需改为 N 股」，再次确认挂单后消失。
- **简报与 Discord**：账户段加「仓位占比 X%（上限 60%），剩余额度 $Y」（宿主算，模型不可用的一天也有；有持仓缺最新收盘时注明占比偏低）；「和昨天比变了什么」加当天的换仓结论、撤回与被取代各一行；名单里 B 那一行写换仓结论或不比较的原因；草稿（没有 agent 的部分）里「钱不够」且该比较的候选写「仓位已到上限，换仓比较未做」；今日操作的换仓卖出一行排在止损触及与另两种卖出指令之后、缺日线之前；Discord 摘要在持仓操作之后加「换仓 B：结论（A）」一行，换仓卖出指令跟在今日操作里。
- **周报**：加「本周换仓」一段，本周的换仓结论各一行。
- **仓位高于上限**：只显示占比，不产生任何卖出指令；之后每只通过确认的候选都按「钱不够」处理。买入后仓位占比高于上限时记成交照常，记一行偏差，复盘多一个标签「买入后超出仓位上限」。
- **没有任何资金记录**：不算仓位占比，不做比较，写「未提供账户资金」。

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

下表先列 v3.1 spec（[2026-11-06 v3.1 设计](../specs/2026-11-06-v3.1-plan-line-account-and-web-design.md)）§18 的 A1–A22，再列 v3.2 spec（[2026-11-08 v3.2 设计](../specs/2026-11-08-v3.2-retrospect-research-and-page-polish-design.md)）§16 的 B1–B19 与 Review Focus 六条，
逐条写下「由哪个测试证明」；测试名都实存于仓库，`tests/` 下的为后端、`web/src/` 下的为前端。没有任何测试能证明的（只能真实运行才能证明的）标「待真实运行」。

### v3.1 新增（A1–A22）

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

### v3.2 新增（B1–B19，spec §16）

| # | 标准 | 证明 |
| --- | --- | --- |
| B1 | 已平仓计划的复盘行：计划说的、实际做的、R 与 `outcome` 一致；偏差标签八个代码各至少一例，`not_filled` 记照做 | `tests/test_retrospect.py::test_tags_each_code_once`（八代码 + `not_filled`=照做逐字断言）、`test_plan_record_origin_kinds_and_groups`（行的内容与分组键）、`test_plan_record_normalizes_repo_round_tripped_r_multiple`（R 倍数经仓储回读与 `outcome` 一致） |
| B2 | 被替换 / 被取消 / agent 否决的不打 `not_bought_in_zone`；过期没买且价格进过区间打它 | `tests/test_retrospect.py::test_not_bought_in_zone_only_for_expired`（三种非用户决定的终态不打；只有 `expired` 打；从未进区间 → `not_filled`） |
| B3 | 假设结果：成交价、离场价、R 与手算一致；收盘跌破止损前未成交；日线用完进行中；替换与取消不影响模拟 | `tests/test_retrospect.py::test_hypothetical_fill_then_initial_stop_gap_exit`（成交价 `min(开盘, 入场上沿)`、跳空离场、R 手算）、`test_hypothetical_close_below_stop_before_fill`、`test_hypothetical_never_in_zone`、`test_hypothetical_trailing_and_max_hold_next_open`、`test_hypothetical_unfinished_marks_to_last_close`；模拟器不读状态、只按价位与日线算，替换与取消不进它的输入 |
| B4 | 规则检验：样本不足的组没有胜率与平均 R 的数字；真实与假设两张表互不相加 | `tests/test_retrospect.py::test_rule_stats_threshold_and_separation`（门槛内给数、门槛外只给样本数；真实只计已平仓 `planned`、假设只计已离场的假设结果） |
| B5 | 复盘在空库上返回空记录与「还没有已结束的计划」，不报错 | `tests/test_v3_retrospect.py::test_empty_database_gives_empty_view`（records 空、empty 真、rules 各组空、weekly 有周界）、`web/src/lib/home/RetrospectPanel.test.ts`（「shows the empty message when there is nothing to review」） |
| B6 | 周报只在每周最后一个交易日的运行里产出并推送；同一周重跑不重推 | `tests/test_daily_job.py::test_week_end_writes_and_pushes_weekly_once`（周五产出 `weekly_md`、多推一条、重跑不重推）、`test_midweek_has_no_weekly`（周三不产出也不多推） |
| B7 | 发起研究：建记录、记 `created`；已有有效计划或观察中研究时拒绝并返回那份；同一 `request_id` 重放返回原行 | `tests/test_v3_tools_research.py::test_start_research_preview_confirm_idempotent_and_rejections`（预览逐字、写入、重放幂等、`research_exists` / `active_plan_exists` 各带 detail）、`test_start_research_replay_across_days`（跨日重放同一 `request_id` 仍回原行）。注：spec B7 原文「加关注列表」按审阅记录 R1 改为「进采集目标」，不写关注列表（见 B15） |
| B8 | 条件四种各有到了 / 没到 / 判不出；缺日线不推进 `checked_through`；到记一次 `triggered` 重跑不重复；到期无触发 `expired` | `tests/test_research_rules.py::test_condition_met_each_kind_hit_miss_and_short_history`（四种条件的到了 / 没到逐例直测；「判不出」的均线历史不足一例）、`test_condition_met_empty_bars_is_none_for_level_kinds`（三种价位条件空日线 → None，参数化小矩阵）、`test_condition_met_empty_bars_is_none_for_ma`（均线条件空日线 → None）、`tests/test_v3_research_review.py::test_missing_bar_does_not_advance_and_catches_up`（缺日线不判不推进、补上后同一天还能判出）、`test_triggered_once_and_rerun_idempotent`、`test_expires_when_valid_until_passes_without_trigger` |
| B9 | 待办里出现条件已到的研究；agent `ready` → 计划带 `research_id`、研究记 `planned`；brief 模式 `watch` 带新条件被拒；`reject` → `rejected` | `tests/test_v3_agenda.py::test_research_items_and_slots_ignore_research_plans`（research 待办参与完成判定）、`tests/test_v3_tools_research.py::test_ready_then_save_plan_links_research_and_skips_slots`、`test_brief_mode_watch_with_new_condition_is_rejected`（拒 `condition_needs_user`，原条件不变）、`test_conclude_reject_ends_research` |
| B10 | 研究计划不占名额：3 份规则计划满员时照常保存；替换受害者只从规则计划里选；同一股票第二份有效计划仍被拒 | `tests/test_v3_tools_plan.py::test_research_plan_ignores_slots_and_replacement`（满员 + 同日已替换过，研究计划照常保存、没有规则计划被替换、研究记 `planned`——「名额仍 0」由此推出，无单独计数断言）、`test_replacement_skips_watching_ticker_like_agenda`（替换候选跳过观察中研究股票——「只从规则计划里选」的另一面由此覆盖）；一只一份有效计划由 `uq_v3_plans_one_active_per_ticker` 保证（`tests/test_migration_034.py::test_one_active_plan_per_ticker_is_a_db_constraint`） |
| B11 | 研究计划入场下沿收盘 − 3.5 ATR 通过、− 4.5 ATR 拒；规则名单计划 − 2.5 ATR 仍拒 | `tests/test_v3_tools_plan.py::test_research_plan_entry_floor_relaxed`（三例齐）、纯函数层 `tests/test_v3_tool_validation.py::test_entry_floor_follows_entry_atr` |
| B12 | 简报：有研究时出现「研究」段与每份一行；条件已到写「条件已到，待复看」；缺日线写「缺日线，未复查」；没有研究时整段不出现；Discord 有研究条件行；页脚含「金额单位美元」 | `tests/test_v3_brief.py::test_research_section_states_and_absent_when_empty`（四种状态文案、排在待入场计划之后、空时不出现）、`test_changes_include_research_lines`、`test_discord_research_triggered_line_after_new_plans`、`test_footer_says_usd` |
| B13 | 页面：研究卡片两组、放弃两步确认、「发起研究」预填助手输入框；复盘块三部分；记成交日期与时间控件拼带美东偏移的 ISO（夏令时与冬令时各一例）；金额与价格两位小数带 `$` | `web/src/lib/home/ResearchCards.test.ts`（两组、放弃两步确认、「发起研究」回调预填）、`web/src/lib/chat/ChatPanel.test.ts`（prefill 预填并聚焦输入框）、`web/src/lib/home/RetrospectPanel.test.ts`（周汇总 / 执行复盘 / 规则检验三部分）、`web/src/lib/home/TradeForm.test.ts`（拼出带美东偏移的 `executed_at`）、`web/src/lib/format.test.ts`（`newYorkIso` 夏令时 / 冬令时 / 切换日七例、`moneyText` 两位小数带 `$`） |
| B14 | `price_value` 取到分三个断言 | `tests/test_workbench_labels.py::test_price_value_two_decimals`（`40.5→40.50`、`98.000000→98.00`、`123.4567→123.46` 逐字）、`tests/test_v3_brief.py::test_price_text_trims_storage_zeros_without_rounding` |
| B15 | 研究采集：观察中研究的股票进每日采集目标；关注列表与筛选范围不因研究而变 | `tests/test_v3_collect.py::test_target_facts_include_watching_research_not_watchlist`（采集目标含研究股票，关注列表不变） |
| B16 | 一只股票同时两份观察中研究被数据库约束拒绝 | `tests/test_migration_035.py::test_one_watching_research_per_ticker_is_a_db_constraint` |
| B17 | 研究股票同时在名单前 N 只：不进二次确认清单，待办仍可完成；名单行标「研究中」 | `tests/test_v3_agenda.py::test_research_items_and_slots_ignore_research_plans`（confirmations 跳过、研究复看计入完成判定）、`tests/test_v3_view.py::test_candidate_marked_研究中`（名单行 `has_research` 与「研究中」）、`tests/test_v3_tools_read.py::test_get_candidates_has_research` |
| B18 | 待入场计划修订止损后 `initial_stop` 同步；研究计划修订价位时入场下限按放宽值 | `tests/test_v3_tools_plan.py::test_revise_pending_stop_updates_initial_stop`（同步且事件 before / after 都带它——直接测试）；放宽下限的护栏由 `tests/test_v3_tool_validation.py::test_entry_floor_follows_entry_atr` 与 save_plan 路径的 `test_research_plan_entry_floor_relaxed` 证明，`revise_plan` 对带 `research_id` 的计划传同一个 `entry_atr`（R7）是源码推导（修订路径本身无端到端测试，已披露的空缺保留） |
| B19 | 没选定模型的周末交易日仍产出并推送周报；错误日不产出 | `tests/test_daily_job.py::test_no_model_week_end_still_pushes_weekly`（draft 日 `weekly_pushed` 为真、周报单独推送）；错误日不产出无直接测试：错误路径不调周报、`weekly_pushed` 恒为 False（`daily/job.py`，spec §5.4 / R5）——源码推导，已披露的空缺保留；错误日的推送语义由 `test_error_day_pushes_once_with_stop_warning_line` 覆盖 |

### v3.2 Review Focus 六条（实施 plan）

| # | 焦点 | 证明 |
| --- | --- | --- |
| 1 | 延续过的待入场计划过期：窗口从保存日的下一个交易日算到最终 `valid_until`，不漏中间交易日 | `tests/test_retrospect.py::test_window_spans_from_first_validity_to_last_valid_until` |
| 2 | 研究股票缺日线：不判、不推进 `checked_through`、写「缺日线，未复查」；补上后还能判出条件到了 | 复查层的直接测试：`tests/test_v3_research_review.py::test_missing_bar_does_not_advance_and_catches_up`（不判、`checked_through` 不动、补上后判出）、`test_midweek_gap_stops_cursor_and_catches_up`；「缺日线，未复查」文案的断言在简报侧（B12 的 `tests/test_v3_brief.py::test_research_section_states_and_absent_when_empty`）与页面卡片（`ResearchCards.test.ts`），研究复查测试本身不覆盖文案 |
| 3 | 简报会话 agent 给 `watch` 且带新条件：拒 `condition_needs_user`，原条件与有效期不变 | `tests/test_v3_tools_research.py::test_brief_mode_watch_with_new_condition_is_rejected`（拒 `condition_needs_user` 与原条件保留是直接测试）；「有效期不变」在拒绝路径上无单独断言——拒绝先于 `env.write`、不落任何字段（源码推导） |
| 4 | 3 份规则计划满员、同一天已替换过，研究计划保存照常成功、不记 `replaced`、名额仍 0 | `tests/test_v3_tools_plan.py::test_research_plan_ignores_slots_and_replacement`（直接测试：保存成功、三份规则计划仍是 pending、研究记 `planned`；「不记 `replaced` / 名额仍 0」由仍是 pending 推出，无单独的 replaced 事件或名额计数断言——源码推导部分如实标注） |
| 5 | 记成交日期落在美东夏令时 / 冬令时切换日附近：拼出的 ISO 偏移分别是 `-04:00` 与 `-05:00`，后端解析得同一时刻 | 前端直接测试：`web/src/lib/format.test.ts` 的 `newYorkIso` 七例（含按时刻取偏移的切换日两例）、`web/src/lib/home/TradeForm.test.ts`（表单拼 `executed_at`）；「后端解析得同一时刻」无直接测试——`datetime.fromisoformat` 按偏移解析（源码推导） |
| 6 | 空库：复盘接口与页面显示「还没有已结束的计划」，简报没有「研究」段，周报在没有记录的周也能产出 | `tests/test_v3_retrospect.py::test_empty_database_gives_empty_view`、`tests/test_v3_brief.py::test_weekly_renders_with_no_records` |

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
| 14 | 流式对话 | `tests/test_api_chat.py::test_turn_streams_and_second_turn_sees_the_first`、`web/src/lib/chat/sse.test.ts`、`web/src/lib/chat/ChatPanel.test.ts` |
| 15 | 隔离（专用 Pi 目录、自带工具禁用、环境白名单） | `tests/test_runtime_pi.py::test_command_line_and_session_arguments`、`test_env_whitelist_passes_proxy_and_pi_vars_only` |
| 16 | 简报运行期间提问得到提示 | `tests/test_api_chat.py::test_brief_running_is_409`、`web/src/lib/chat/ChatPanel.test.ts`（409 显示「正在生成简报，请稍后再问」） |
| 17 | 换模型（设置面板） | `tests/test_api_settings.py::test_put_saves_and_reads_back`、`test_put_rejects_unavailable_and_keeps_store`、`test_options_lists_runtimes_providers_models`（面板调用的就是这三个接口；面板 UI 本身无专门前端测试） |
| 18 | 假实现（无 Pi、无网络跑全套） | `tests/test_runtime_fake.py`（全文件）、`tests/test_daily_session.py`（全部用假运行时） |
| 19 | 超时与取消 | `tests/test_api_chat.py::test_cancel_ends_stream_and_next_turn_works`、`tests/test_runtime_chat.py::test_timeout_marks_the_message_timeout`、`tests/test_runtime_pi.py::test_stalled_stream_times_out_and_kills` |

另：真实 Pi 客户端的冒烟（v3 设计验收的做法）：v3.0 在 2026-11-06 做过一次（见 [v3-facts.md](v3-facts.md) 冒烟记录）；v3.1 在 2026-11-07 上线当天经用户授权用 `launchctl kickstart` 触发已安装的 LaunchAgent 做了一次，记录见下。

### v3.1 上线冒烟（2026-11-07）

- 前两次触发暴露两处问题，已修：plist 缺 `WorkingDirectory`（launchd 在 `/` 下启动，包未装进 venv，`ModuleNotFoundError`）；日线采集耗尽 launchd 的 256 个文件描述符
  （yfinance 的 `cache_get` 是 64 条的 lru_cache，缓存的 curl_cffi 响应各自钉住 `hard_deadline` 线程的 curl 句柄；采集后残留约 200 个描述符、峰值约 345，修复后峰值约 147）。
- 第三次：退出码 0，stdout `{"session": "2026-11-06", "status": "final", "attempts": 1, "complete": true, "pushed": true}`；简报会话 315 秒、token 输入 53495 / 输出 13220；
  名单前五逐项有结论（1 否决、3 确认并各存一份待入场计划，第 5 名因名额已满未处理）；`v3_briefs` 为 `final` 且已推送 Discord；页面 `/api/briefs/latest` 返回该简报。
- 验收 11（连续 3 个定时交易日无人干预）仍待真实运行。
