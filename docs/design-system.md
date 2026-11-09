# 设计标准

前端（`web/`，Svelte 5 + Tailwind v4 + shadcn-svelte / Bits UI）的唯一设计规则：原则、令牌、布局、状态词汇、数据块五态、组件清单与 API、交互、图标与图表、文案。首版是 [v2.10 spec](specs/2026-10-30-v2.10-design-system-and-ui-refactor-design.md) §4 的原样落地，再补上实际的文件路径、props 与令牌取值；此后**组件、令牌或交互规则变了，与代码同一个提交更新本文**（spec 冻结，本文是活的）。

**看效果**：`cd web && npm run dev`，打开 `#_ui`（组件画廊，只在开发模式存在，不进生产包）。画廊里有令牌、通用层全部组件、状态词汇、领域原语、数据块五态、图标，浅 / 深色可切换；改组件先在画廊里看一遍。

**守护**：`tests/test_frontend_design_guards.py` 静态检查 `web/src`：页面里不得有原色类名（`red-600`、`white` 等）、十六进制 / `rgb()` 字面量、emoji、`<style>` 块、裸 `<button>`、`{@html}`、原生 `confirm` / `alert` / `prompt`。违反即测试失败。守护是正则检查，仍能绕过的写法：`confirm(变量)`、CSS 命名色（`text-[red]`、`style="color:red"`）、`<svelte:element this="button">`——审查时要看，不能只靠测试。

---

## 1. 原则

1. **先把状态说清，再谈好看。** 每个数据块必须能表达五态之一：加载中 / 无数据 / 没算 / 降级 / 失败（第 5 节）；没有「看起来正常但其实是空」的状态。
2. **数字可追溯。** 任何统计数字旁边或所在块的页头，能看到口径版本（`calc_version` 之类）、样本量、截止时间或数据时点中的相关项；没有样本量不给均值（沿用 v2.7 规则）。
3. **视图与抽屉紧凑，对话从容。** 表格、卡片与抽屉保持紧凑密度；对话正文 13px / 行高 1.55，限宽 760px 居中，回合之间 14px。
4. **一致胜于新颖。** 同一含义全站一个写法、一个颜色、一个组件；新需求先查标准，标准没有再加组件并更新标准。
5. **可读的中文。** 中文字体栈、中文标点、「」引号、数字与单位之间半角空格、时间标注时区（沿用 `fmtBeijing`）。

## 2. 令牌

令牌定义在 `web/src/styles/tokens.css`（值），`web/src/styles/app.css` 用 `@theme inline` 映射成 Tailwind 类（`bg-card`、`text-muted-foreground`、`border-border`……）。**一律十六进制**（`#rrggbb` / `#rrggbbaa`）：画布类图表库（lightweight-charts、ECharts 一类）的颜色解析不认 oklch 或 `var()` 链，重建图表时要遵守。只采用 shadcn 的命名，不采用其默认取值；没有 `--secondary`、`--accent`、`--sidebar-*`。

| 组 | 令牌 | 规则 |
| --- | --- | --- |
| 表面 | `--background`、`--card`、`--muted`、`--border`、`--ring` | 页面底、卡片 / 表面、次级表面、边框、焦点圈；只这五个层次（另有 `--popover`、`--input` 给浮层与输入框） |
| 文字 | `--foreground`、`--muted-foreground` | 正文与次要文字两级；不再有第三种灰。注意 `muted` 是次级**表面**，次要**文字**是 `text-muted-foreground` |
| 强调 | `--primary` / `--primary-foreground` | 唯一的强调色（浅色墨黑，深色反色），用于主按钮、当前导航、链接 |
| 状态 | `--success`、`--warn`、`--destructive`、`--info`、`--running`、`--neutral` 各带 `-foreground` | 对应状态语气（第 4 节）；**页面里不得出现原色类名**（`red-600`、`amber-700`、`sky-500`……） |
| 涨跌 | `--up`、`--down` | 美股约定绿涨红跌，只用于价格 / 收益数值，不用于状态 |
| 图表 | `--chart-rule`、`--chart-own`、`--chart-agent`、`--chart-fill`、`--chart-grid` | 规则 / 自定 / Agent 三种来源各一色（图表里的价位线、对比线共用），其余序列用 `--chart-1…5`（色相与三种来源色、涨跌色拉开，K 线上的 MA 线不会与价位线撞色；`tokens.test.ts` 检查） |
| 字号 | `text-xs` 12 / `text-sm` 13 / `text-base` 14 / `text-lg` 16 / `text-xl` 20，五档界面字号（行高 16 / 18 / 20 / 24 / 28）；对话另用 `text-chat` 13px / 1.55 | 页面正文 `text-sm`，表格 `text-xs` 或 `text-sm`，页面标题 `text-lg`，不再用 `text-2xl` 以上 |
| 间距 | Tailwind 4px 基数，组件内 `1 / 2 / 3`，块间 `4 / 6`，页内分段 `8` | 不用 `.65rem`、`1.2rem` 这类自写值 |
| 圆角 | 控件 `--radius` 8px，卡片 / 输入框 12px，用户气泡 14px | `rounded-xs/sm/md/lg` → 8px，`rounded-xl` → 12px，`rounded-2xl/3xl/4xl` → 14px；`rounded-full` 不变 |
| 阴影 | `--shadow-1`、`--shadow-2` 只给 Popover / Sheet / Dialog 等浮层 | 卡片、按钮与输入框无阴影 |
| 动效 | 过渡 150ms，仅颜色与透明度；运行中状态用边框脉冲（`animate-running`，沿用 `NodeGraph`）；不做进场动画 | `prefers-reduced-motion` 时全部关闭 |
| 深浅色 | `[data-theme]`（`light` / `dark`）两套令牌值；首次打开跟随系统（`prefers-color-scheme`），用户切换后记本地（`lib/theme.ts`） | 两套都要满足 WCAG AA 对比（正文 4.5:1、次要文字 3:1）；每个状态色都在两套里各调一次 |

### 令牌实际取值

对比度是该色作文字时落在 `--card` 上的 WCAG 对比度（`web/src/styles/tokens.test.ts` 逐条断言 ≥ 4.5，深浅两套）。

| 令牌 | 浅色 | 深色 | 浅色对比度 | 深色对比度 |
| --- | --- | --- | --- | --- |
| `--background` | `#fafafa` | `#0f0f0f` | | |
| `--card` / `--card-foreground` | `#ffffff` / `#1a1a1a` | `#171717` / `#ededed` | 17.40 | 15.31 |
| `--popover` / `--popover-foreground` | `#ffffff` / `#1a1a1a` | `#171717` / `#ededed` | | |
| `--muted` | `#f2f2f2` | `#222222` | | |
| `--border` / `--input` / `--ring` | `#ececec` / `#e0e0e0` / `#1a1a1a` | `#2a2a2a` / `#3a3a3a` / `#ededed` | | |
| `--foreground` | `#1a1a1a` | `#ededed` | 17.40 | 15.31 |
| `--muted-foreground` | `#6b6b6b` | `#9a9a9a` | 5.33 | 6.37 |
| `--primary`（`-foreground`） | `#1a1a19`（`#ffffff`） | `#ededed`（`#111111`） | 17.42 | 15.31 |
| `--success`（`-foreground`） | `#15803d`（`#ffffff`） | `#22c55e`（`#052e16`） | 5.02 | 7.87 |
| `--warn`（`-foreground`） | `#b45309`（`#ffffff`） | `#f59e0b`（`#1c1002`） | 5.02 | 8.35 |
| `--destructive`（`-foreground`） | `#dc2626`（`#ffffff`） | `#f87171`（`#2a0a0a`） | 4.83 | 6.48 |
| `--info`（`-foreground`） | `#1d4ed8`（`#ffffff`） | `#60a5fa`（`#0a1a33`） | 6.70 | 7.05 |
| `--running`（`-foreground`） | `#6d28d9`（`#ffffff`） | `#a78bfa`（`#1e1136`） | 7.10 | 6.59 |
| `--neutral`（`-foreground`） | `#475569`（`#ffffff`） | `#94a3b8`（`#0b0f14`） | 7.58 | 6.99 |
| `--up` / `--down` | `#15803d` / `#dc2626` | `#22c55e` / `#f87171` | 5.02 / 4.83 | 7.87 / 6.48 |
| `--chart-rule` / `-own` / `-agent` | `#2563eb` / `#b45309` / `#7c3aed` | `#60a5fa` / `#f59e0b` / `#a78bfa` | 5.17 / 5.02 / 5.70 | 7.05 / 8.35 / 6.59 |
| `--chart-fill` | `#64748b2e` | `#94a3b838` | | |
| `--chart-grid` | `#e5eaf0` | `#1f2a38` | | |
| `--chart-1…5` | `#0e7490 #a21caf #475569 #4d7c0f #be185d` | `#22d3ee #e879f9 #94a3b8 #a3e635 #f472b6` | 均 ≥ 4.99 | 均 ≥ 6.7 |
| `--shadow-1` / `--shadow-2` | `0 1px 2px rgba(15,23,42,.06)` / `0 14px 35px rgba(15,23,42,.08)` | `0 1px 2px rgba(0,0,0,.4)` / `0 14px 35px rgba(0,0,0,.5)` | | |
| `--radius` | `8px`（只在 `:root`，形状不随主题变） | | | |

标题（`Card.Title`、`PageHeader`、`Section` 与壳面板标题）统一 `font-serif font-semibold`（600）：`"Songti SC", "Noto Serif CJK SC", "Source Han Serif SC", Georgia, serif`；正文保持 Inter 与现有中文系统字体栈，不下载新字体。

要点：

- 浅色 `--success` / `--up` 从旧的 `#16a34a`（白底 3.3:1，不达标）改为 `#15803d`。
- 状态色淡底上的同色文字达不到 AA（浅色下 `success` / `warn` / `destructive` 只有约 3.7–4.0），所以 `StatusPill` 的文字用 `text-foreground`，语气由圆点、边框和淡底表达（第 4 节）。
- 浮层遮罩没有专用令牌：浅色 `bg-foreground/40`，深色 `dark:bg-background/70`。
- 画布库读不了 CSS 变量：重建图表时要在挂载与主题切换时把上表解析成十六进制（见第 8 节）。

## 3. 布局

- **应用壳**：`lib/shell/Shell.svelte` 提供三栏：左栏展开 208px / 折叠 56px，右侧抽屉 288px，中心由 `center` snippet 提供（完整视图满宽，对话由消费者限宽 760px）。顶栏切换仪表 / 行情 / 定时任务，当前面板再点一次关闭；桌面 `Esc` 不关抽屉。< 1024px 左栏与抽屉共用一个 `Sheet`，左右切换，始终只有一个面板打开；关闭后焦点回到触发按钮。`Home.svelte` 继续作为工作台根组件；Task 4 提供壳，Task 6 接入根页面，`app.svelte` 的主题、Tooltip 与 Toaster 沿用。
- **页头**（每个路由与每个大段落一致）：标题 + 口径 / 时点 / 版本的一行次要文字 + 右侧动作区（**至多一个主按钮**，其余描边或链接）。用 `PageHeader`。
- **分段**：`Section`（标题 + 可选说明 + 内容），不嵌套超过两层；卡片只给「一个对象」（一份计划、一张复盘卡、一个数据族），列表性内容用表格不用卡片堆。
- **网格**：桌面 ≥ 1024px 两栏或四栏；768–1023 两栏；< 768 单栏。
- **窄屏（< 768px）**：字段多的表以**行转卡**呈现（每行变一张紧凑卡，字段名在左，`DataTable` 的 `cards="narrow"`）；其余表格横向滚动。不做 < 375px。判断窄屏用 `useNarrow()`（`web/src/lib/ui/narrow.svelte.ts`，查询串 `(max-width: 767px)`）；没有 `matchMedia`（jsdom、旧浏览器）时按桌面。

## 4. 状态词汇与语气

全站状态词统一进 `web/src/lib/ui/domain/status.ts` 一张表：`TONES`（领域 → 键 → 语气）与 `LABELS`（领域 → 键 → 中文）。`StatusPill` 只认这张表，**不认识的键在开发模式抛错、在生产显示中性灰加原键**。

| 语气 | 令牌 | 用于 |
| --- | --- | --- |
| `success` | `--success` | 成功、已完成、已审定、已连接、`adopted`、`executed`、`knowledge.active` |
| `warn` | `--warn` | 降级、部分成功、待审定、起草中、`not_computed`、`insufficient` |
| `danger` | `--destructive` | 失败、起草失败、`error`、`rejected`、需要重新登录 |
| `info` | `--info` | 入场期 `entry`、修订版、新版本、提示类说明 |
| `running` | `--running` | 运行中、复查中、登录中、轮询中（带脉冲） |
| `neutral` | `--neutral` | 跳过、未开始、已结束、过期、被取代、已平仓、只手动、旧版本运行 |

颜色只表达语气，**文字必须同时出现**（不只靠颜色）。

### 领域与逐键语气

每个域对应 v2 后端的一个状态来源（后端已随 v2 移除，「来源」列保留为各域含义的出处；词表不再有测试对照后端枚举）。同名键按域区分：`node` / `run` 的 `pending`（未开始）是 `neutral`，`execution.pending`（入场期，未成交）是 `info`，`connection.pending`（登录中）是 `running`。

| 域 | 来源（后端） | 键 → 语气 |
| --- | --- | --- |
| `node` | `stage_runs.status` + 页面推导的 `pending` / `running` | `success` 成功；`partial`、`degraded` 警告；`failed` 危险；`skipped`、`pending` 中性；`running` 运行中；运行一览的 `missing`（缺证据）警告、`not_recorded`（未记录）中性，文字照抄 `run_overview.py` 的 `STATUS_TEXT` |
| `run` | `job_reports.status`（`success` / `degraded` / `error`）+ `node` 的全部值 | 同 `node`，另 `error` 危险 |
| `chain` | `timeline.CHAIN_STATES` | `entry` 信息；`holding` 成功；`exiting` 警告；已结束类 `legacy`、`invalidated`、`closed_by_decision`、`position_closed`、`superseded`、`expired` 全部中性 |
| `fetch` | `data_fetch_runs.status` | `success` 成功；`partial` 警告；`failed`、`error` 危险；`skipped` 中性；`running` 运行中 |
| `agent` | `agent.STATUSES` | `ok` 成功；`pass`（不建议买入）、`skipped` 中性；`rejected`、`error` 危险 |
| `connection` | `agent_connections.status` + `no_sharing` / `unknown` | `connected` 成功；`pending` 运行中；`needs_login` 危险；`no_sharing` 警告；`disconnected`、`unknown` 中性 |
| `card` | `retro.CARD_STATUSES` | `reviewed` 成功；`to_review`、`drafting` 警告；`draft_failed` 危险 |
| `retro` | 周 / 月复盘运行结果（`RETRO_STATUS_TEXT`） | `reviewed`、`generated` 成功；`to_review`、`drafting` 警告；`draft_failed`、`error` 危险；`reused`、`already_sent` 中性；`reminded` 信息 |
| `push` | `RETRO_PUSH_TEXT`（其余「未发送」原因原样显示） | `sent` 成功；`manual_run`、`not_sent` 中性 |
| `execution` | `execution.STATUSES` ∪ `CHAIN_STATUSES` ∪ `retro.EXECUTION_STATUSES` | `adopted`、`executed` 成功；`pending` 信息；`unplanned`（计划外成交）警告；`out_of_validity`、`not_executed` 中性 |
| `knowledge` | `knowledge_status.status` | `active` 成功；`archived` 中性 |
| `instruction` | `timeline.INSTRUCTION_TEXT` | `hold` 中性；`sell_half` 警告（有动作要做）；`exit` 危险（最醒目，旧界面就是红色加粗）；`no_plan` 信息 |
| `system` | `services/health.py` 的检查状态（`up` / `degraded` / `down` / `unknown`） | `up` 成功；`degraded` 警告；`down` 危险；`unknown` 中性 |
| `consistency` | `derivation.CONSISTENCY_TEXT`（计划推导核对） | `consistent` 成功；`mismatch` 危险；`incomplete` 警告 |
| `block` | 数据块状态（`DataBlock`、`MetricValue`、`SampleSize`、`CutoffTime`） | `not_computed` 没算、`insufficient` 样本不足，都是警告 |
| `account` | `accounts.status` | `active` 成功；`closed` 中性 |
| `paper_order` | `paper_orders.status` | `pending` 信息；`filled` 成功；`voided`、`expired`、`cancelled` 中性；`rejected` 危险 |
| `paper_decision` | `paper_decisions.status` | `ok` 成功；`skipped` 中性；`rejected` 危险 |
| `reconcile` | 账户对账结果（`RECONCILE_TEXT`） | `matched` 成功；`mismatch` 危险（文字「待核对」）；`no_ledger` 中性（文字「暂无账本权益」） |
| `research_request` | 请求五态 + 结论版本 error 的共用展示域 | queued 信息；running 运行中；waiting_data 警告；concluded 成功；closed 中性；error 危险 |
| `proposal` | roadmap_proposals.status | proposed 信息；approved 成功；rejected、superseded 中性 |
| `portfolio_verdict` | 硬检查 verdict | ok 成功；blocked 危险；deferred 警告 |
| `data_request` | data_requests.status | queued 信息；fetched 成功；failed 危险；deferred 警告 |
| `feature_flag` | 功能当前值 | enabled 成功；disabled 中性 |
| `roadmap_version` | roadmap_versions.status | planned 信息；in_progress 运行中；engineering_done 成功；user_acceptance_pending 警告；done 成功；dropped 中性 |
| `plan` | v3.1 计划线状态（`GET /api/plans` 的 `status`） | pending 待入场信息；open 已买入成功；closed、expired、invalidated、replaced、cancelled 五种已结束状态全部中性 |
| `plan_action` | 「下一个交易日的操作」五种（`next_action.kind`，v3.1 spec §7.4）；固定文案来自接口 `text`，`stop_order` 的「需上调」变体用 `StatusPill` 的 `text` 覆盖 | stop_hit_unresolved 危险、sell_next_open 警告（两种警示语气）；unchecked、stop_order 信息、hold 中性（常规） |
| `stop_order` | 持仓的止损挂单状态（`stop_order_status`） | none 中性；needs_raise 警告；confirmed 成功 |
| `research` | v3.2 研究五态（`GET /api/research` 的 `status`，spec §6.1） | watching（观察中）信息；planned（已转计划）成功；rejected、expired、dropped 三个终态中性 |
| `retro_tag` | v3.2 复盘偏差标签（`execution.retrospect` 的 `tag.code`）+ `followed`（照做） | followed 成功；not_filled（未买入：价格未进入场区间）中性、不算偏差；七个偏差代码（not_bought_in_zone、entry_out_of_zone、over_bought、stop_order_missing、late_sell_after_stop、late_sell_after_instruction、early_sell）全部警告。带 N 的两个（晚卖 N 个交易日）由组件拼句后经 `StatusPill` 的 `text` 传入，表里存不带数字的文案 |
| `regime` | 市场热度 | risk_on 进攻成功；neutral 中性；risk_off 防守警告；insufficient 样本不足警告 |
| `etf_state` | ETF 强弱 | strong 强成功；mixed 混合、weak 弱中性 |
| `write` | 写入结果 | written 已写入成功；preview 待确认警告；replayed 重放中性 |
| `daily_run` | 定时任务展示 | final 成功；draft 降级警告；error 失败危险；unfinished 未做完警告；pending 待运行中性；running 运行中 |

### 合并实施的五组中文（v2.12–v2.15 词表）

`research_request`、`proposal`、`portfolio_verdict`、`data_request`、`feature_flag` 五个域的中文直接写在 `status.ts` 的 `LABELS` 里（原来的独立常量与跨端 sync 测试已随 v2 移除）。

与 spec §4.4 表的差异（D16）：表里写的 `green` / `yellow` / `red`、`done`、`gate_hold` 等在后端没有对应枚举（数据族卡显示的是拉取记录状态，即 `fetch` 域），不进词汇表；旧 `StatusPill` 的这几个键无调用方。

`chain.invalidated`（已作废）在表里是 `neutral`，不是 spec 表里「作废 → danger」的字面读法：它是链的终态（`ENDED_STATES`），整行本来就置灰（`muted`），属于「已结束」一类，与 `expired`、`position_closed` 同列；`danger` 留给真正的异常（失败、出错、校验未通过、需要重新登录）。

### 维护 `status.ts`

1. **多了一个状态值**：先在 `TONES` 对应域里加一行键（**每个域保持单行** `domain: { key: 'tone', … },`，键可裸写或用单引号，不用展开运算符——`status.test.ts` 按行检查这个形状），再在 `LABELS` 里补中文，然后才在页面里用。中文直接写在 `LABELS` 里，不从业务模块引用。
2. **新域**：加进 `StatusDomain` 联合、`TONES`、`LABELS` 三处；`status.test.ts` 会检查每个键都有中文、两张表的域一致。
3. **带占位符的文案**（`execution.adopted` 是「已采纳（{date} 成交 {quantity} 股）」）：`StatusPill` 的 `text` 传填好的句子，语气仍取自表。
4. `node` 域比 `run` 多运行一览才有的 `missing`（缺证据）/ `not_recorded`（未记录）；`execution` 域多周复盘 `by_execution` 的键 `unplanned`（计划外成交）。
5. 开发模式未知键会抛 `unknown status: <域>.<键>`，这是有意的：新状态必须先进表。生产环境不抛，显示原键 + 中性灰。

## 5. 数据块五态

每个取数的块（页面、段、卡、表）用同一个 `DataBlock` 包装，必选 `state`：

| 态 | 显示 | 文案规则 |
| --- | --- | --- |
| `loading` | `Skeleton`，形状接近最终内容（`skeleton`：`row` 一行 / `block` 一块 / `table` 表头加五行） | 不写字（容器带 `aria-busy` 与 `aria-label="加载中"`） |
| `empty` | 一行次要文字 | 写**为什么空**：「无持仓」「今日无触发」「暂无成交」——是业务上的「无信号」 |
| `not_computed` | 一行次要文字 + `warn` 徽标「没算」 | 写**缺什么**：「缺资金快照，未计算股数」「样本 3 个，少于 5 个不给均值」——与 `empty` 严格区分（roadmap §2 原则 3） |
| `degraded` | 内容照常显示 + 顶部 `Alert`（warn） | 写**哪部分不全**与原因：「Yahoo 空结果，日线用上一交易日」「概念卡暂不可用：<原因>」 |
| `error` | `Alert`（danger）+ 重试按钮（若可重试） | 原因来自接口 `{"error": "<代码>"}` 的中文映射，不显示原始英文码与堆栈 |

另有 `ready`：只渲染内容。页面级错误（整页取不到）用同一组件撑满内容区（`page`，只对 `loading` / `empty` / `not_computed` / `error` 生效并居中；`ready` / `degraded` 照常满宽渲染内容）；块级错误只占该块。**调用方必须给 `empty` / `not_computed` 传 `message`**（写为什么空、缺什么）；组件内的默认文案（`empty` 「暂无数据」、`degraded` 「部分数据不全」、`error` 「加载失败」）只是兜底，页面上出现兜底文案就是漏写。**禁止**用空白、`—`、`null`、`undefined` 表达以上任一态。`MetricValue`、`CutoffTime`、`SampleSize`、`PlanLevels` 缺值时自带「没算」徽标或文字，不透传 `fmtBeijing` 返回的「—」。

## 6. 组件清单

通用层在 `web/src/lib/ui/<组件>/`（shadcn-svelte 1.7.2 复制来的，样式已按第 2 节改写；Bits UI 2.20.2），领域层在 `web/src/lib/ui/domain/`（自写）。页面**只组合这两层的原语**（v3.1 的首页例外：它自己的组合件在 `web/src/lib/home/`，见 6.2 末），不自己拼样式。

### 6.1 通用层

导入路径形如 `import { Button } from '$lib/ui/button/index'`。

| 组件 | 路径与 API | 规则 |
| --- | --- | --- |
| `Button` | `ui/button/`：`variant`：`primary` / `outline`（默认）/ `ghost` / `link` / `destructive`；`size`：`sm`（28px，表格内）/ `md`（32px，默认）/ `icon`（28px 方形：主题切换、关闭）；`busy`（禁用 + `aria-busy` + 转圈）、`busyText`（忙时替换文案）；传 `href` 渲染成 `<a>` | 一个页头或一段至多一个 `primary`；`outline` 用 `border-input`（1px `--input`）；导航用 `<a href>`，动作类的下划线按钮用 `variant="link"`；`link` 的 `sm` / `md` 都是 `h-auto px-0`，可内嵌在文字里 |
| `Badge` | `ui/badge/`：无 `variant`，只有中性底；传 `href` 渲染成 `<a>` | 不直接用；由 `StatusPill` 加语气 |
| `DataTable`（`Table`） | `ui/data-table/`：`columns: Column<T>[]`（`key`、`header`、`align: 'left' \| 'num' \| 'center'`、`title` 表头提示、`cell` 自定义单元格 snippet、`value` 纯文本取值、`cardLabel`、`hideInCards`）；`rows` 或 `groups: RowGroup<T>[]`（`key`、`label`、`rows`、`muted`、`collapsible`、`open` 初始状态、`onToggle`）；`rowKey`；`caption`；`headerGroups`（分组表头）；`expandable` snippet + `bind:expanded`；`muted(row)`；`cards: 'never' \| 'narrow'`；`ariaLabel`（给 `<table>` 和卡片列表一个可访问名称，不出现可见 caption）；`expandLabel(row, open)`（行展开按钮的可访问名称，默认「展开详情」，例如 `展开 MU` / `收起 MU`）；`pageSize`（默认 50，「显示更多」）；`sticky`（默认 true）。底层 `ui/table/` 的 `Table.Root` 有 `containerClass` | 数字列 `align: 'num'`；状态列用 `StatusPill`；已结束 / 作废 / 被取代的行置灰（`muted`），不删不隐藏；展开按钮可用 `Enter`，名称恒为「展开详情」、状态在 `aria-expanded`；空表接 `DataBlock` |
| `ConfirmDialog`（`Dialog`） | `ui/confirm-dialog/ConfirmDialog.svelte`：`bind:open`、`title`、`consequence`（后果一句话）、`actionLabel`（动作名，不写「确定」）、`destructive`、`busy`、`onConfirm`、`onCancel`、`confirmDisabled`（只禁用动作按钮，如必填项还没填）、`children`（可选的表单体，渲染在说明与按钮之间，如作废理由） | 不可逆动作必经；**确认后不自动关**，调用方做完事再 `open = false`；`busy` 时 `Esc` / 遮罩 / 取消都不响应；初始焦点在「取消」（`onOpenAutoFocus` 显式落到取消按钮，有表单体时也一样；`busy` 时两个按钮都禁用，交给 Bits 默认处理），关闭后焦点回到打开前的元素；底层 `ui/dialog/` 内容渲染在 Portal |
| `Sheet` | `ui/sheet/`：`SheetContent` 的 `side`、`width: 'sm' \| 'lg'`（24rem / 40rem，窄屏全宽，默认 `sm`） | 右侧抽屉 |
| `Tabs` | `ui/tabs/`（Bits `Tabs`） | 只用于**不改地址**的页内页签；方向键 / `Home` / `End` 由 Bits 负责。本版除画廊外没有页面使用它，保留为备用原语 |
| `TabNav` | `ui/tab-nav/TabNav.svelte`：`label`、`items: { href, label, current, badge?, badgeLabel? }[]`；`badgeLabel` 写成角标的 `aria-label` 与 `title`（如「待审定 2 张」），没给就保持原样 | 路由级页签（一个分区的子页）是**导航**：链接 + `aria-current="page"`，不是 `tablist`（D12）。已知取舍：角标的 `aria-label` 挂在无角色的 `<span>` 上（为保持旧测试 `getByLabelText('待审定 N 张')`），非 Chrome 的读屏器不一定读出，所以角标的数字旁仍应有可见文字 |
| `Field` | `ui/field/Field.svelte`：`label`、`id`、`hint`、`error`、`required`；children 得到 `{ id, describedby, invalid }` | 标签在上，说明与错误在下（错误 `role="alert"`，输入框 `aria-describedby`）；必填 `*` 画在标签旁、不在 `<label>` 内；数字字段 `inputmode="decimal"` 右对齐 |
| `Input` / `Textarea` / `Select` / `Label` | `ui/input/`、`ui/textarea/`、`ui/select/`、`ui/label/`；`Select.Trigger` 的 `size`：`sm` / `md` | 放进 `Field` 使用；`Input` 的普通与文件分支都用 `rounded-xl`（12px）。新页面优先用 `Select` / `Collapsible` |
| `Tooltip` | `ui/tooltip/`；`Tooltip.Provider` 在 `app.svelte` 全局提供，默认延迟 300ms | 图标按钮的标签；表头缩写说明 |
| `Popover` | `ui/popover/` | 就地展开的小浮层（同一页同时只开一个、`Esc` 收起） |
| `Collapsible` | `ui/collapsible/` | 折叠段、已结束组。**用法**：`CollapsibleContent` 外面再包 `{#if open}` 时加 `forceMount`（让 `{#if}` 当开关）；Bits 的内容在 jsdom 里挂载即展开时会一直 `hidden`（见 `collapsible.test.ts`）。关闭态的内容留在 DOM 里带 `hidden` |
| `Alert` | `ui/alert/`：`variant`：`info`（默认，`role="status"`）/ `warn` / `danger`（`role="alert"`）；`role` 可覆盖；配 `AlertTitle`、`AlertDescription`、`AlertAction` | 取代各处 `role="alert"` 的 `<p>` |
| `Skeleton` | `ui/skeleton/`：一个 `animate-pulse` 的块，用 `class` 定尺寸；行 / 块 / 表三种预设在 `DataBlock` 里 | |
| `Toast` | `ui/toast.ts`：`notify.success(msg)` / `notify.info(msg)`（3 秒自动消失）、`notify.error(msg)`（不自动消失，带「关闭」按钮）；`Toaster` 在 `app.svelte` 挂载（`ui/sonner/`，主题读 `lib/theme.ts`） | 操作结果；没有 `matchMedia` 时 `Toaster` 不渲染，页面测试里改为断言对 `notify` 的调用 |
| `Separator`、`Card` | `ui/separator/`、`ui/card/`（`Card.Root`、`Header`、`Title`、`Description`、`Action`、`Content`、`Footer`） | 布局用；卡片只给一个对象，`Card.Root` 用 `rounded-xl`（12px）与 1px `border-border`，无阴影 |

### 6.2 领域层

导入路径形如 `import StatusPill from '$lib/ui/domain/StatusPill.svelte'`。

| 组件 | props | 职责 |
| --- | --- | --- |
| `StatusPill` | `{ domain: StatusDomain; value: string; text?: string; title?: string }` | 按第 4 节词汇表显示状态：圆点 + 文字，`running` 语气带 `animate-running`；`text` 覆盖文字（填好占位符的模板），语气不变；`title` 写原因。DOM 带 `data-domain` / `data-status` / `data-tone` |
| `VersionChip` | `{ version: number \| string; latest?: number \| null; href?: string; latestHref?: string; ref?: string }` | 数字版本保持 `第 k 版` 与最新版本链接；合并实施扩展字符串版本用于账户 agent_version，显示真实版本标识；`ref`（`concept:<id>@<v>` / `know:<id>@<v>`）作原生 `title`，同时放 `data-ref` |
| `CutoffTime` | `{ at: string \| null \| undefined; label?: string }`（默认「截止」） | `{label} {fmtBeijing(at)}`，后缀是 `fmtBeijing` 的「北京」（D17，`lib/format.ts` 不改）；`<time datetime>` 的 `title` 是相对时间（「3 小时前」「45 分钟前」「2 天前」「刚刚」）；`at` 为空显示 `StatusPill block.not_computed`，不透传「—」；无法解析的值原样显示、不带提示 |
| `SampleSize` | `{ n: number \| null; min?: number }`（默认 5） | `n = {n}`；`n < min`（含 0）加 `block.insufficient`；`null` 显示 `block.not_computed`（没算） |
| `CalcVersion` | `{ version: string }` | `口径 ` + 等宽代码样式的版本号 |
| `MetricValue` | `{ value: number \| null \| undefined; raw?: string \| null; unit?: string; digits?: number; signed?: boolean; percent?: boolean; currency?: boolean; tone?: 'updown' \| 'none' }`（`digits` 默认 2，`tone` 默认 `none`） | 等宽数字（`tabular-nums`），单位前加半角空格（`12 股`），`percent` 的值是小数（0.032 → `3.2%`，无空格），`signed` 给正数加 `+`；`updown` 时正 `text-up`、负 `text-down`、零不着色；`-0.00` 归一成 `0.00`；`null` / `undefined` / `NaN` 显示 `block.not_computed`；传非空 `raw` 时优先显示原文，不解析或舍入后端 Decimal 字符串，`unit` 仍保留，长值可换行。`currency`（v3.2 spec §7）时 `body` 走 `format.ts` 的 `moneyText`（`raw` 与 `value` 都适用：两位小数、前缀 `$`、负数 `-$12.34`、空仍显示 `block.not_computed`），`$` 前缀替代单位后缀，`tone` 的符号判断不变 |
| `PlanLevels` | `{ low: number \| null; high: number \| null; stop: number \| null; target1: number \| null; shares?: number \| null; lead?: string; sharesLabel?: string }` | 一行标准写法 `入场 $95.10–$97.30 · 止损 $91.40 · T1 $104.70`（v3.2 spec §7：价格是金额，两位小数带 `$` 前缀），有股数时加 ` · 建议 120 股`；`lead`（默认「入场」）换掉区间前的词，`sharesLabel`（默认「建议」，空字符串则只写 `120 股`）换掉股数前的词（区间用 U+2013）；props 是数字，而接口里的价位是字符串（`"95.10"`）：**调用方用 `Number()` 转换**再传（接口不变；无法转成数字的值按缺失处理）。缺的价位写「没算」，四个都缺时是一个 `block.not_computed` 徽标。`PlanLevels.test.ts` 钉住的是数字格式（`$` 两位小数、U+2013、缺值写「没算」）和 `lead` / `sharesLabel` 的默认值 |
| `RefLink` | `{ ref: string; onFact?: (key) => void; onCall?: (ref) => void; expanded?: boolean }` | 一条引用的渲染：`fact:` 有 `onFact` 时是调用回调的链接式按钮，否则纯文字；`call:` 有 `onCall`（调用方能展开）时是 `aria-expanded` 按钮，否则纯文字；其余（`know:`、`concept:`、`user:` 等）纯文字。列表外壳与展开的工具调用区域归调用方 |
| `SourceBadge` | `{ source: string; fallback?: boolean }` | 数据来源名（等宽）；`fallback` 时加淡警告底与文字「备源」 |
| `DataBlock` | `{ state; message?; skeleton?: 'row' \| 'block' \| 'table'; onRetry?; retryLabel?: string; page?: boolean; children? }` | 第 5 节五态包装；`degraded` 先 `Alert` 再渲染 `children`，`error` 有 `onRetry` 时加一个按钮（默认「重试」），`page` 撑满内容区（`min-h-[50vh]`） |
| `PageHeader` | `{ title: string; level?: 1 \| 2 \| 3; meta?: Snippet; actions?: Snippet }`（`level` 默认 2） | 标题 + `meta` 次要行 + 右侧一个动作区（`data-slot="page-header-actions"`）。**迁移时每个标题保持原有级别**（测试按 `level` 查标题） |
| `Section` | `{ title?: string; level?: 2 \| 3 \| 4; description?: string; actions?: Snippet; children: Snippet }`（`level` 默认 3） | 分段；有标题时是 `aria-labelledby` 命名的 `region`；级别规则同上 |
| `Icon` | `{ name: IconName; size?: 16 \| 20; label?: string }` | `@lucide/svelte` 的薄包装；无 `label` 时 `aria-hidden="true"`，有则 `role="img"` + `aria-label`。`icons.ts` 是显式映射表（逐个 `import X from '@lucide/svelte/icons/<name>'`），**加图标就是在这张表加一行** |

`icons.ts` 现有图标：`calendar-days`、`search`、`clipboard-list`、`database`、`book-open`、`wrench`、`settings`、`activity`、`bell`、`sun-moon`、`x`、`check`、`chevron-down`、`chevron-right`、`info`、`external-link`、`refresh-cw`、`triangle-alert`、`circle-x`、`loader`、`menu`、`panel-left-close`、`panel-left-open`、`microscope`、`map`、`toggle-left`、`message-square`、`layout-grid`、`message-circle`、`panel-left`、`wallet`、`trending-up`、`list`、`history`、`clock`、`plus`、`arrow-up-right`、`corner-down-right`。

### 6.2.1 首页组件（`web/src/lib/home/`）

v3.1 首页（`routes/Home.svelte`）的组合件：只组合通用层与领域层原语，取数走 `lib/api.ts` 的 `get/post/put`；接口字段见 `types.ts`（照抄 Task 5 的接口契约表，金额与价位是字符串，展示用 `MetricValue` 的 `raw`，不做浮点运算）。所有写入接口先 `confirmed=false` 拿 `preview`，经 `ConfirmDialog` 复述确认后才 `confirmed=true`（A18）。

布局（`Home.svelte`）：v3.2.1 工作台根组件组装三栏 `Shell`；左栏七个完整视图与对话列表，中间按 hash 切换，右侧三种侧览。对话常驻，仅隐藏；切视图与切对话不取消已开始的 SSE，一轮结束后仍在原对话展示。对话正文与输入限宽 760px，正文 13px / 1.55，助手保持纯文本；简报仍用 Markdown。`⌘/Ctrl+K` 在任意视图切回最近对话并聚焦输入。

| 组件 | props | 职责 |
| --- | --- | --- |
| `types.ts` | — | 接口契约的类型与 `errorText`（把 `{"error": "code: 说明"}` 的中文部分取出来） |
| `markdown.ts` + `Markdown.svelte` | `{ source }` | 简报 Markdown 解析成块结构（标题 / 段落 / `-` 列表 / `|` 表格 / `**粗体**`，不认识的行按段落），用 `{#each}` 渲染，表格用 `ui/table`；不输出 HTML 字符串、不用 `{@html}` |
| `TodayActions.svelte` | `{ actions, onChanged? }` | 今日操作：空列表渲染一张小卡片「今天没有需要操作的事项」（用户 2026-11-07，取代 spec §12「整块不出现」）；`stop_hit_unresolved` 排第一；每行可带进对话；「确认已挂单」两步确认；卖出类动作带「记成交」 |
| `BriefPanel.svelte` | `{ session? }` | 共享 briefs / briefFor 缓存，session 指定初始日期；手动切换与刷新保留当前选择。今日简报：最终稿 / 草稿（「二次确认未完成」Alert）/ 无简报的原因；`Select` 切历史（`GET /api/briefs`） |
| `PlanCard.svelte` | `{ card, expanded?, onChanged? }` | 一份计划卡：状态、价位（`PlanLevels`）、下一个交易日的操作（`plan_action`）；持仓另有浮动盈亏、已持有天数、止损单状态；已结束显示平仓结果；展开拉 `GET /api/plans/{id}` 看变化记录；pending 显示「最新收盘高于入场区间」、可取消计划；open 可调高止损；根节点带 `id="plan-{plan_id}"`；研究卡链接使用 `#plans/{id}`；expanded 控制初始展开，仍可手动折叠；「带进对话」走 ContextChip 事件 |
| `PlanCards.svelte` | `{ id?, onChanged? }` | 三组：持仓、待入场、已结束（折叠）；空组走 `DataBlock` 的 `empty` 态显示原因；「持仓」组标题旁「补录持仓」打开不带 `plan_id` 的 `TradeForm`（宿主补录计划，spec §6.6）；订阅 plans(group)，写入后 invalidateAll；id 自动展开变化记录（含已结束及默认页之外的对象） |
| `AccountStrip.svelte` | `{ account?, onChanged? }` | 默认读 today store（可显式传 account 快照）；写入后 invalidateAll 并追加本地操作说明。账户条：现金 / 总资产 / 浮动盈亏 / 已实现盈亏；现金为负显示 `notes`；「记出入金」「对账」入口 |
| `TradeForm.svelte` / `CashFlowForm.svelte` / `ReconcileForm.svelte` | `{ bind:open, onDone? }`（TradeForm 另有 `ticker`/`planId`/`shares` 预填） | 三个写入表单（Dialog）：预填、失败保留草稿与 `request_id`、表单内 `Alert` 显示原因；提交先拿 `preview` 再 `ConfirmDialog` 确认写入。v3.2 spec §7：TradeForm 的「时间」拆成「日期（美东）」「时间（美东）」两个原生控件（`type="date"` / `type="time" step="60"`，默认 `nowInNewYork()`，提交拼 `newYorkIso` 带美东偏移）；出入金与对账的日期也是 `type="date"` 控件，提交口径不变 |
| `SettingsSheet.svelte` | `{ bind:open, onSaved? }` | 设置（`Sheet`）：运行时 / 提供方 / 模型，选项来自 `GET /api/settings/options`，每项可在 `Select` 与自由 `Input` 间切换；保存 `PUT /api/settings`，错误用 toast 显示原因 |
| `ResearchCards.svelte` | `{ id?, onChanged?, onStartResearch? }` | 研究两组（v3.2 spec §10）：观察中、已结束（折叠）；空组走 `DataBlock` 的 `empty` 态；观察中组标题旁「发起研究」按钮调 `onStartResearch('研究 ')`（研究 + 空格，用户补股票与论点；页面打开助手并预填，研究在对话里发起，spec §10「股票留空时预填『研究 』」）；订阅 research(group)，写入后 invalidateAll；id 打开相应对象（含已结束及默认页之外的对象） |
| `ResearchCard.svelte` | `{ card, expanded?, onChanged? }` | 一张研究卡：股票、`StatusPill`（`research` 域）、第几天、论点、条件与最新收盘（`MetricValue` 的 `currency`）、显示文案（优先级：真触发 `triggered_session` → 无条件 → 缺日线 `missing_bar`（stale 日线上的 condition_met 盖不过它）→ 当日条件已到 → 均线日线不足 → 观察中，从卡片字段推导）、agent 结论一句；三种价位条件的文案由 `condition.kind` + `moneyText(condition.level)` 拼（`收盘站上 $105.00` 等，spec §7），`close_above_ma` 沿用服务端文案；「已转计划 #id」是锚点链接（`#plans/{plan_id}`，打开计划完整视图）；展开拉 `GET /api/research/{id}` 看时间线；观察中的「放弃研究」两步确认（`POST /api/research/{id}/drop`，`confirmed=false` 先拿 preview）；expanded 控制初始展开，仍可手动折叠；「带进对话」走 ContextChip 事件 |
| `RetrospectPanel.svelte` | `无` | 复盘（v3.2 spec §10）：顶部周汇总（`Select` 切周，本周与之前 8 周，值用各周周五；已结束 / 照做 / 有偏差、R 合计、已实现盈亏与周初周末总资产带 `$`）、中间执行复盘 `ui/table`（每份已结束计划一行：股票、起止、计划说的 `PlanLevels`、实际做的、R、偏差标签 `StatusPill`（`retro_tag` 域，带 N 的句子经 `text` 传入）、假设结果一句）、底部规则检验两张表（真实成交 / 假设结果）× 三个分组键，样本不足的组只显示样本数与「样本不足」，表头上方一行接口 `note`；执行复盘记录「可翻」：`limit` 阶梯 20 → 50 → 100（契约上限 100，不扩接口，「显示更多」按钮）。周汇总除数字与金额外还显示接口携带的 §5.4 明细：偏差各出现几次、已平仓各份一行（R 与离场原因、标签）、未买各份一行（结束原因与假设结果）、本周名单（新上榜 / 掉出 / 再次上榜最多）。`empty` 时整块「还没有已结束的计划」（B5）；共享 retrospectFor(week,limit)，全站写入后重取且保留当前周与条数 |

### 6.2.2 工作台壳与取数（`web/src/lib/shell/`、`stores/`）

| 组件 / 模块 | API | 职责 |
| --- | --- | --- |
| `Shell` | `{ title, session, counts, chats, currentChatId, onNewChat, onOpenSettings, center: Snippet, drawerPane: Snippet<[DrawerPane]> }` | 三栏与响应式 Sheet；内容由两个 snippet 注入；不取业务数据 |
| `LeftRail` | `{ counts, chats, currentChatId, route, onNewChat, onNavigate? }` | 左栏宽度、折叠按钮与分段内容 |
| `SegmentSwitch` | 无 props，消费 `layout` | 快捷入口 / 对话，`aria-pressed`；折叠时竖排图标 |
| `QuickEntries` | `{ counts, route, onNavigate? }` | 今日、账户、行情、计划与持仓、研究、复盘、定时任务七个 hash 链接与 `aria-current`；折叠时角标改圆点 |
| `ChatList` | `{ chats, currentChatId, onNewChat, onNavigate? }` | 按 `last_message_at` 北京日期分今天 / 本周（周一起）/ 更早；无标题写「新对话」，只读标签；折叠后只剩新开动作 |
| `TopBar` | `{ title, session, onToggleDrawer(pane, trigger), onOpenNav(trigger), onOpenSettings }` | 标题、数据时点与图标面板开关；无运行时写「还没有运行记录」 |
| `Drawer` | `{ drawerPane: Snippet<[DrawerPane]> }` | 桌面侧览容器；关闭时 `hidden` |
| `layout.svelte.ts` | `layout`、`toggleNav()`、`setSegment(segment)`、`toggleDrawer(pane)` | 存 `ws.nav.collapsed`（`1` / 缺省展开）、`ws.nav.segment`（`quick` / `chats`）、`ws.drawer`（`dash` / `market` / `jobs` / `closed`）；缺省 quick、dash，存储禁用仍可用 |
| `router.ts` | `parseHash(hash): Route`、`hrefOf(route)` | chat / view / gallery；七视图，plans / research 可带数字 id；未知回空 chat；`#_ui` 保持开发画廊 |
| `resource.svelte.ts` | `createResource<T>(path: string \| (() => string \| null), opts?)` → `{ data, state, error, load(), invalidate() }` | 响应式读取自动订阅与首次加载；写入后失效保留旧数据直至新结果抵达；最后消费者卸载后停止自动重取；重新挂载取脏缓存；空路径 empty，旧请求不覆盖新路径 / 新请求 |
| `stores/index.ts` | `today`、`market`、`runs`、`ledger`、`ledgerForLimit(limit)`、`account`、`plans(group)`、`research(group)`、`retrospect`、`retrospectFor(week,limit)`、`briefs`、`briefFor(session?)`、`chats`、`invalidateAll()` | 一接口一共享缓存，分组缓存按组复用；写入完成后统一失效 |
| `stores/types.ts` | Task 3 页面契约类型 | 新类型以真实 JSON 为准：金额与数量字符串，Today 账户 cash / equity 可空；旧 home 类型的数字 quantity 在接入旧组件时须显式转换 |

消费者在 Svelte 模板中直接读 `today.state` / `today.data` / `today.error`（或在 effect 中读）即订阅；组件外按需 `await today.load()`，普通非响应式读取不触发网络。动态路径函数可以读响应式状态；返回 null 时清空数据并转 empty。`invalidateAll()` 只立即刷新仍有消费者的资源，其余标脏等下次挂载。`errorDetails` 缺省开启以读取接口错误，错误经中文映射后展示；取数只经 `lib/api.ts`，不新加依赖。

### 6.2.3 数据视图与抽屉（`web/src/lib/views/`、`drawer/`）

| 组件 / 模块 | API | 职责 |
| --- | --- | --- |
| `registry.ts` | `ToolName`（35 个字面量）、`REGISTRY: Record<ToolName, ViewEntry>`、`viewFor(tool)` | 条目 `{ component, title(data) }`；未知工具回退 `GenericCard`；组件渲染调用时快照，动作跳对象页读取现状 |
| `CallMeta` | `{ tool; ref; session; calledAt; write?: 'written' \| 'preview' \| 'replayed' }` | 裸工具名、真实调用编号与时点，不为页面接口编造调用编号 |
| `labels.ts` | `FIELD_LABELS`、`TOOL_LABELS` | 工具中文标题与接口字段中文，覆盖后端共用 fixtures 的顶层键 |
| `cards/SourceLine` | `{ meta: CallMeta; calcVersion? }` | 等宽来源行 `{tool} · call:N · {session}`；无 session 用北京时间；无 ref 写 `{tool} · 调用中`；有计算版本才附加 |
| `cards/CardFrame` | `{ title, data, meta?, calcVersion?, badges?, contextChip?, children, emptyMessage? }` | 所有快照共用卡片标题、写入徽标、来源行、中文错误、空列表 / null 与待确认复述；单一对象在页头「就这个继续问」，多个计划 / 研究各行提供动作；错误 / preview 不显示写入动作；错误信封保留来源和中文原因，并抑制所有写入状态徽标 |
| `cards/MarketCard` | `{ data: MarketData; meta?; full?; pane?; onBring?(chip) }` | 缺省四格与前两只领涨、ETF 前 6 行；full 展示 ETF 全表、宏观与关注表；pane 独立启用三行区制与全部 ETF / 关注表，不显示宏观，按 RS20 降序且不改原数据；三种用法共用表格。watch.change_pct 已为百分点，展示前转为 MetricValue 的小数口径 |
| `cards/PlanSnapshotCard` | `{ data: unknown; meta }` | 原始计划 / 包装行 / 列表及持仓快照；价位用 Number 转成 PlanLevels，缺值写「没算」；打开计划、预填 TradeForm、「就这个继续问」 |
| `cards/ResearchSnapshotCard` | `{ data: unknown; meta }` | 原始研究的状态、论点、条件、有效期；打开研究与「就这个继续问」 |
| `cards/AccountCard` | `{ data: unknown; meta? }` | 账户数字、持仓、对账差额、仓位快照来源；记出入金 / 对账复用已有表单。页面接口省略 meta，快照必须提供真实 CallMeta |
| `cards/LevelsCard` / `TradeCard` / `NoteCard` | `{ data: unknown; meta }` | PlanLevels 与 ATR / 口径；成交、计划变化、止损单、平仓结果；保存的文字与写入徽标 |
| `cards/AgendaCard` / `BarsTable` | `{ data: unknown; meta }` | facts agenda 四组清单（空组「无」）；日期 / 开高低收 / 量表格，数字右对齐 |
| `cards/GenericCard` | `{ data: unknown; meta }` | 平面字典键值两列；字典列表取字段并集前 8 列并写剩余列数；其他 JSON 前 40 行可展开；空与缺值有原因 |
| `ResourceBlock` / `RunStatus` | `{ resource, emptyMessage, children }` / `{ brief }` | 共享 store 五态与重试，no_run 映射 empty；daily_run 状态，unfinished 额外徽标 |
| `drawer/DashboardPane` | `{ onBring(chip) }` | 今日操作 / 账户 / 持仓与计划 / 研究中 / 市场温度五块；今日操作复用两步确认接口和 TradeForm；止损确认保留预览请求的计划、价格与 request_id，独立于成交表单的预填目标；每个对象行可带进对话 |
| `drawer/MarketPane` | `{ onBring(chip) }` | 共享 MarketCard 的 pane 模式，打开完整行情链接；回调只触发一次 |
| `drawer/JobsPane` | `{ onBring(chip) }` | 最近 4 次与下次北京时间、研究条件检查、当前纽约日历周一至周五窗口内的周报；同一天多次运行分别展示 |
| `AccountView` / `MarketView` / `JobsView` | 无 props，自取共享 store | 账户三张流水（20→40→60→80→100，保留已作废行与原因）、行情 full、任务计划与 20 行运行表；任务行分别展开 usage / 耗时 / 模型，链接 `#today?session=日期` |
| `views/context.ts` | `ContextChip = { kind: 'ticker' \| 'plan' \| 'research'; ticker; id }`、`bringToChat(chip)`、`actionCompleted(text)` | 浏览器内事件 `workbench:bring` 的 detail 为 ContextChip，`workbench:action` 的 detail 为 `{ text }`；导出事件常量与事件类型；无 window 时不操作 |

`ledgerForLimit(limit)` 校验 1–100 的整数并按 limit 共享缓存；20 复用 `ledger`，其余请求 `/api/ledger?limit=N`。`invalidateAll()` 同时刷新仍挂载的各条数资源；账户显示更多后保持当前条数，任一表达到当前条数时才能继续请求，最高 100。

动作只有 confirmed=true 成功后才调用一次 `invalidateAll()` 与 `actionCompleted`。复用表单自己的 toast，不重复提示；表单的 onDone 不带提交数据，完成文案只写「已记录成交」「已记录出入金」「已记录对账」等真实概述，不把可能已被编辑的预填值冒充成交结果。Task 6 的 Home 在挂载时监听两个事件、卸载时移除监听：bring 交给对话芯片输入，action 追加到活动 / 最近对话的当前回合，仅本页保留，不持久化。抽屉直接调用 onBring，避免同一动作再发一次全局事件。

### 6.2.4 对话与完整视图组装（`web/src/lib/chat/`）

| 组件 / 模块 | API | 职责 |
| --- | --- | --- |
| `ChatPanel` | `{ chatId: string \| null, prefillChips?, prefillText?: {text,seq}, onWrote() }`；`focusInput()` / `appendFeedback(text)` | 按对话保存消息与回合，保持原 SSE 的归属；start 帧保存 agent_session，tool_end 按出现顺序认领同名占位；写工具完成的正常轮 invalidateAll / onWrote 一次，纯文字轮刷新对话标题；取消与只读、忙碌提示保持原文；取消请求未返回前禁止下一轮，防止旧取消误伤新轮 |
| `Turn` / `types.ts` | `{ turn: Turn, chatId, history? }`；`Turn` / `Block` | 直播按文字与工具的事件顺序；历史卡在文字之前；文字保持纯文本与换行；call:N 经 RefLink 滚动到本回合的卡片并暂时高亮 |
| `ToolCard` | `{ block, chatId, agentSession, lazy? }` | loading 占位用 TOOL_LABELS（get_market 为「市场环境」），结果交 registry；历史进入视口才请求，观察器卸载断开；失败可重试且不妨碍文字；空 ref 显示「调用已完成，结果不可追溯」，旧 null 结果显示「这次调用早于 v3.2.1，没有保留结果」，截断结果明确降级；中断时只把尚在调用中的块转 error，取消写「已停止等待，调用结果未确认」、错误或意外断流写「调用未完成」，已完成块保留；无调用编号不提供重试 |
| `ContextChips` / `context.ts` | `{ chips, onRemove? }`；`formatPrefix` / `parsePrefix` | ticker / plan / research 三种芯片；固定空格分隔前缀，只有开头连续的合法格式会还原；发送清空，可移除，普通方括号保留原文 |
| `QuickPrompts` / `ChatEmpty` | `{ onSend, onResearch, disabled? }` / `{ onNewChat }` | 五句固定快捷入口，发起研究只预填「研究 」；无对话时说明与新建入口，快捷提问可创建对话后发送 |
| `sse.ts` | `readEvents(response,onEvent,signal?)` | 从 home 迁入；任意字节分片解析，取消时主动结束等待中的 reader，无需等下一帧 |
| `TodayView` / `PlansView` / `ResearchView` / `RetrospectView` | `{ session? }` / `{ id? }` / `{ id?, onStartResearch }` / 无 | PageHeader + 原有业务组件的薄包装；今日读取 `#today?session=`；计划 / 研究详情支持已结束组及列表之外的 id |

`retrospectFor(null,20)` 复用 `retrospect`；其余缓存按 week 与 limit（20 / 50 / 100）区分。`briefFor()` 与 `briefFor('latest')` 相同，历史按 session 复用；`briefs` 是简报列表。全部纳入 invalidateAll，刷新不重置 UI 日期与分页。页面操作仍先 preview 后 confirmed，复用表单自己的 toast；本地完成说明经 actionCompleted 追加到当前 / 最近对话，不入库。

开发画廊增加「视图卡片」（十一种用途的共用工具 fixtures）与「壳」（展开 / 折叠 / 窄屏三态缩略）；画廊仍只在开发包启用。

### 6.3 已知取舍

- **`DataTable` 宽表在容器内滚动，放得下才让表头吸顶。** 容器 `overflow` 一旦不是 `visible`，`sticky` 就只在容器内生效、贴不到视口顶部；而容器是 `visible` 时，比可用宽度还宽的表会经由一串 grid 祖先（`min-width: auto`）把整页撑出横向滚动（v2.10 验收在「今日」计划表 1024–1280px 下复现）。所以 `sticky`（默认）的表用 `ResizeObserver` 和窗口 `resize` 测量：先临时把容器设成 `overflow-x: auto`，看表格宽度是否不超过容器；放得下时容器为 `max-lg:overflow-x-auto lg:overflow-visible`（≥ 1024px 表头吸顶），放不下时一律 `overflow-x-auto`（在容器内横向滚动，表头不吸顶）。`<table>` 上的 `data-fits` 记录测量结果；测量前、没有 `ResizeObserver`（jsdom）时和 `sticky={false}` 的表都按放不下处理，在任何宽度都在容器内滚动。放在 `Card` 里的表即使放得下也在卡片内滚动（`lg:in-data-[slot=card]:overflow-x-auto`）：`Card` 自带 `overflow-hidden`（圆角裁切），粘性表头在卡片里本来就贴不到视口。
- **`RefLink` 的动作类引用**（`fact:` / `call:`）是 `Button variant="link"`，用 `text-[length:inherit]` 继承所在文字的字号并允许选中文字。`know:` / `concept:` 原来链到知识页与概念卡，两者已随 v2 移除，现在是纯文字。
- **`StatusPill` 的文字不着状态色**，原因见第 2 节要点：淡底上的同色文字在浅色主题下对比度不足 AA。
- **卡片视图里的长状态标签换行。** `Badge` 默认不换行（表格列宽按内容算，允许换行会把中文标签挤成一字一行）；`DataTable` 的 `cards="narrow"` 卡片里，徽标改为可换行、最宽到单元格宽度、圆角 10px（单行时仍是胶囊形），所以「Agent 未给出计划：模型调用失败，以下为规则基线」这样的长标签在 375px 下折成两行，不撑出屏幕。其他窄屏场景里放长标签，要自己给容器留宽度或换写法。
- **`CutoffTime` 的相对时间用原生 `title`**，不用 `Tooltip`（不依赖 Provider，任何地方可用）。`VersionChip` 的 `ref` 同理。

## 7. 交互规范

- **动作**：一个页头或一段至多一个 `primary` 按钮；不可逆动作（作废、断开、归档、撤回等）必须经 `ConfirmDialog`，确认按钮写动作名不写「确定」。不使用原生 `confirm` / `alert` / `prompt`。
- **提交与幂等**（沿用 `request_id` 规则）：提交中按钮 `busy`（并在处理函数里加重入保护，进行中再点不重复提交）、表单只读；失败保留草稿与 `request_id` 并在表单内 `Alert` 显示原因；成功后 `notify.success`。
- **轮询**（长任务）：进行中显示 `running` 徽标与已等待时间；超时给出明确文案，不自动无限重试。
- **链接与按钮**：导航用 `<a href="#…">`（可中键打开、可复制），动作用 `<button>`（即 `Button`）；二者视觉不同（链接下划线，按钮无下划线）。
- **键盘**：所有可交互元素 `focus-visible` 显示 `--ring` 焦点圈；`Esc` 关闭最上层浮层；表格行展开可用 `Enter`。由页面自己打开的 `Sheet` / 对话框（不是 `Dialog.Trigger`）在打开时记下 `document.activeElement`，并在 `onCloseAutoFocus` 里把焦点还给它；`ConfirmDialog` 自带初始焦点（取消）与关闭后回到打开前元素。
- **表单**：标签在上；错误在字段下且 `aria-describedby`；数字字段 `inputmode="decimal"`、右对齐；日期时间字段注明时区。
- **表格**：数字列右对齐等宽；文本列左对齐；状态列用 `StatusPill`；表头可带工具提示；超过 50 行的表分页或「显示更多」（接口已有 `total`）；已结束 / 作废 / 被取代的行置灰（`muted`），不删不隐藏。
- **深浅色**：切换即时生效（图表重绘），所有颜色经令牌；`lib/theme.ts` 的 `getTheme()` / `toggleTheme()` / `onThemeChange()`。
- **安全**：不使用 `{@html}`，不得为了样式引入。
- **文案**：错误原因一律中文映射，不显示英文码；相对时间只作工具提示，正文写绝对时间。

## 8. 图标与图表

- 图标只用 `@lucide/svelte`，通过 `Icon` 组件；`icons.ts` 的映射表里仍有 v2 导航用过的图标，画廊「图标」段全部展示。不再出现 emoji（守护检查）。
- **图表组件已随 v2 移除**（ECharts 与 lightweight-charts 的封装 `lib/charts/` 连同两个依赖都已删除）。下面的规则保留，供以后重建图表时沿用：
  - 图表颜色在挂载与主题切换时从令牌（`--foreground`、`--muted-foreground`、`--border`、`--chart-*`）读成十六进制的主题对象，主题切换时销毁重建；系列色按来源角色（规则 / 自定 / Agent）而不是序号；图表背景用 `card`。
  - K 线：涨跌用 `--up` / `--down`；标记与价位线按 `--chart-rule` / `--chart-own` / `--chart-agent`；作废计划用 `--chart-fill` 淡显；代码里不写十六进制。候选标记（买入 / 卖出箭头、观察圆点）不带计划来源，统一用规则色 `--chart-rule`，方向靠形状；成交标记 B / S 用 `--up` / `--down`；价位线按计划来源三色。lightweight-charts 的线条描边直接认 `#rrggbbaa`，所以 `--chart-fill` 的透明度对作废计划的价位线有效。
- 图表旁必有文字说明口径与时点（`CalcVersion` + `CutoffTime`）。
- 分界标记（markLine）用线型区分种类：虚线 dashed、实线 solid、点线 dotted，不能只靠颜色区别。

## 9. 文案

沿用现状并写进标准：全角标点，「」作引号，中西文与数字之间半角空格，单位紧跟数字（`12 股`、`-3.2%`），时间 `fmtBeijing`（输出末尾带「北京」），版本 `第 k 版`，口径名用等宽字体（`CalcVersion`）。

---

## 10. 怎么改这份标准

| 要做的事 | 步骤 |
| --- | --- |
| 加一个状态值 | 第 4 节「维护 `status.ts`」 |
| 加一个图标 | 在 `web/src/lib/ui/domain/icons.ts` 加 import 与映射一行，画廊「图标」段自动出现；`Icon.test.ts` 里的名字表同步 |
| 改令牌取值 | 改 `web/src/styles/tokens.css` 两套（浅 / 深）；`tokens.test.ts` 会检查两套令牌集合一致、十六进制、AA 对比度；本文第 2 节取值表同步 |
| 加一个通用 / 领域组件 | 先查本文第 6 节有没有；没有再加，补 `*.test.ts`、画廊一段、本文第 6 节一行，同一个提交 |
| 加一个页面 | 只组合第 6 节的原语；数据块用 `DataBlock`，状态用 `StatusPill`，缺值写「没算」；跑 `tests/test_frontend_design_guards.py` |

相关：设计依据见 [v2.10 spec](specs/2026-10-30-v2.10-design-system-and-ui-refactor-design.md)，实施步骤见 [v2.10 plan](plans/2026-10-30-v2.10-design-system-and-ui-refactor.md)。
