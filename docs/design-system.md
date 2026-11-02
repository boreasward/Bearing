# 设计标准

前端（`web/`，Svelte 5 + Tailwind v4 + shadcn-svelte / Bits UI）的唯一设计规则：原则、令牌、布局、状态词汇、数据块五态、组件清单与 API、交互、图标与图表、文案。首版是 [v2.10 spec](specs/2026-10-30-v2.10-design-system-and-ui-refactor-design.md) §4 的原样落地，再补上实际的文件路径、props 与令牌取值；此后**组件、令牌或交互规则变了，与代码同一个提交更新本文**（spec 冻结，本文是活的）。

**看效果**：`cd web && npm run dev`，打开 `#_ui`（组件画廊，只在开发模式存在，不进生产包）。画廊里有令牌、通用层全部组件、状态词汇、领域原语、数据块五态、图标，浅 / 深色可切换；改组件先在画廊里看一遍。

**守护**：`tests/test_frontend_design_guards.py` 静态检查 `web/src`：页面里不得有原色类名（`red-600`、`white` 等）、十六进制 / `rgb()` 字面量、emoji、`<style>` 块、裸 `<button>`、新增 `{@html}`、原生 `confirm` / `alert` / `prompt`；状态词汇表必须覆盖后端能产生的每个状态值。违反即测试失败。守护是正则检查，仍能绕过的写法：`confirm(变量)`、CSS 命名色（`text-[red]`、`style="color:red"`）、`<svelte:element this="button">`——审查时要看，不能只靠测试。

---

## 1. 原则

1. **先把状态说清，再谈好看。** 每个数据块必须能表达五态之一：加载中 / 无数据 / 没算 / 降级 / 失败（第 5 节）；没有「看起来正常但其实是空」的状态。
2. **数字可追溯。** 任何统计数字旁边或所在块的页头，能看到口径版本（`calc_version` 之类）、样本量、截止时间或数据时点中的相关项；没有样本量不给均值（沿用 v2.7 规则）。
3. **密度优先。** 这是一个每天读几分钟的工作台，不是落地页：默认紧凑密度，一屏尽量多放信息，用层级而不是留白分隔。
4. **一致胜于新颖。** 同一含义全站一个写法、一个颜色、一个组件；新需求先查标准，标准没有再加组件并更新标准。
5. **可读的中文。** 中文字体栈、中文标点、「」引号、数字与单位之间半角空格、时间标注时区（沿用 `fmtBeijing`）。

## 2. 令牌

令牌定义在 `web/src/styles/tokens.css`（值），`web/src/styles/app.css` 用 `@theme inline` 映射成 Tailwind 类（`bg-card`、`text-muted-foreground`、`border-border`……）。**一律十六进制**（`#rrggbb` / `#rrggbbaa`）：lightweight-charts 4 与 ECharts 5 的颜色解析不认 oklch 或 `var()` 链。只采用 shadcn 的命名，不采用其默认取值；没有 `--secondary`、`--accent`、`--sidebar-*`。

| 组 | 令牌 | 规则 |
| --- | --- | --- |
| 表面 | `--background`、`--card`、`--muted`、`--border`、`--ring` | 页面底、卡片 / 表面、次级表面、边框、焦点圈；只这五个层次（另有 `--popover`、`--input` 给浮层与输入框） |
| 文字 | `--foreground`、`--muted-foreground` | 正文与次要文字两级；不再有第三种灰。注意 `muted` 是次级**表面**，次要**文字**是 `text-muted-foreground` |
| 强调 | `--primary` / `--primary-foreground` | 唯一的强调色（青绿），用于主按钮、当前导航、链接 |
| 状态 | `--success`、`--warn`、`--destructive`、`--info`、`--running`、`--neutral` 各带 `-foreground` | 对应状态语气（第 4 节）；**页面里不得出现原色类名**（`red-600`、`amber-700`、`sky-500`……） |
| 涨跌 | `--up`、`--down` | 美股约定绿涨红跌，只用于价格 / 收益数值，不用于状态 |
| 图表 | `--chart-rule`、`--chart-own`、`--chart-agent`、`--chart-fill`、`--chart-grid` | 规则 / 自定 / Agent 三种来源各一色（K 线价位线、推导、对比图共用），其余序列用 `--chart-1…5`（色相与三种来源色、涨跌色拉开，K 线上的 MA 线不会与价位线撞色；`tokens.test.ts` 检查） |
| 字号 | `text-xs` 12 / `text-sm` 13 / `text-base` 14 / `text-lg` 16 / `text-xl` 20，仅五档（行高 16 / 18 / 20 / 24 / 28） | 页面正文 `text-sm`，表格 `text-xs` 或 `text-sm`，页面标题 `text-lg`，不再用 `text-2xl` 以上 |
| 间距 | Tailwind 4px 基数，组件内 `1 / 2 / 3`，块间 `4 / 6`，页内分段 `8` | 不用 `.65rem`、`1.2rem` 这类自写值 |
| 圆角 | `--radius` 一个值（6px），`rounded-full` 只给徽标与头像 | 其余 `rounded-*` 全部映射到同一个值 |
| 阴影 | `--shadow-1`（表面）、`--shadow-2`（浮层） | 类名 `shadow-1` / `shadow-2` |
| 动效 | 过渡 150ms，仅颜色与透明度；运行中状态用边框脉冲（`animate-running`，沿用 `NodeGraph`）；不做进场动画 | `prefers-reduced-motion` 时全部关闭 |
| 深浅色 | `[data-theme]`（`light` / `dark`）两套令牌值；首次打开跟随系统（`prefers-color-scheme`），用户切换后记本地（`lib/theme.ts`） | 两套都要满足 WCAG AA 对比（正文 4.5:1、次要文字 3:1）；每个状态色都在两套里各调一次 |

### 令牌实际取值

对比度是该色作文字时落在 `--card` 上的 WCAG 对比度（`web/src/styles/tokens.test.ts` 逐条断言 ≥ 4.5，深浅两套）。

| 令牌 | 浅色 | 深色 | 浅色对比度 | 深色对比度 |
| --- | --- | --- | --- | --- |
| `--background` | `#f6f8fa` | `#0b0f14` | | |
| `--card` / `--card-foreground` | `#ffffff` / `#111827` | `#121822` / `#e5e9f0` | 17.74 | 14.62 |
| `--popover` / `--popover-foreground` | `#ffffff` / `#111827` | `#161d29` / `#e5e9f0` | | |
| `--muted` | `#f0f3f6` | `#1a2330` | | |
| `--border` / `--input` / `--ring` | `#d9e2ec` / `#c4cfdb` / `#0f766e` | `#243040` / `#34425a` / `#2dd4bf` | | |
| `--foreground` | `#111827` | `#e5e9f0` | 17.74 | 14.62 |
| `--muted-foreground` | `#6b7280` | `#94a3b8` | 4.83 | 6.94 |
| `--primary`（`-foreground`） | `#0f766e`（`#ffffff`） | `#2dd4bf`（`#042f2e`） | 5.47 | 9.56 |
| `--success`（`-foreground`） | `#15803d`（`#ffffff`） | `#22c55e`（`#052e16`） | 5.02 | 7.81 |
| `--warn`（`-foreground`） | `#b45309`（`#ffffff`） | `#f59e0b`（`#1c1002`） | 5.02 | 8.29 |
| `--destructive`（`-foreground`） | `#dc2626`（`#ffffff`） | `#f87171`（`#2a0a0a`） | 4.83 | 6.44 |
| `--info`（`-foreground`） | `#1d4ed8`（`#ffffff`） | `#60a5fa`（`#0a1a33`） | 6.70 | 7.00 |
| `--running`（`-foreground`） | `#6d28d9`（`#ffffff`） | `#a78bfa`（`#1e1136`） | 7.10 | 6.54 |
| `--neutral`（`-foreground`） | `#475569`（`#ffffff`） | `#94a3b8`（`#0b0f14`） | 7.58 | 6.94 |
| `--up` / `--down` | `#15803d` / `#dc2626` | `#22c55e` / `#f87171` | 5.02 / 4.83 | 7.81 / 6.44 |
| `--chart-rule` / `-own` / `-agent` | `#2563eb` / `#b45309` / `#7c3aed` | `#60a5fa` / `#f59e0b` / `#a78bfa` | 5.17 / 5.02 / 5.70 | 7.00 / 8.29 / 6.54 |
| `--chart-fill` | `#64748b2e` | `#94a3b838` | | |
| `--chart-grid` | `#e5eaf0` | `#1f2a38` | | |
| `--chart-1…5` | `#0e7490 #a21caf #475569 #4d7c0f #be185d` | `#22d3ee #e879f9 #94a3b8 #a3e635 #f472b6` | 均 ≥ 4.99 | 均 ≥ 6.7 |
| `--shadow-1` / `--shadow-2` | `0 1px 2px rgba(15,23,42,.06)` / `0 14px 35px rgba(15,23,42,.08)` | `0 1px 2px rgba(0,0,0,.4)` / `0 14px 35px rgba(0,0,0,.5)` | | |
| `--radius` | `6px`（只在 `:root`，形状不随主题变） | | | |

要点：

- 浅色 `--success` / `--up` 从旧的 `#16a34a`（白底 3.3:1，不达标）改为 `#15803d`。
- 状态色淡底上的同色文字达不到 AA（浅色下 `success` / `warn` / `destructive` 只有约 3.7–4.0），所以 `StatusPill` 的文字用 `text-foreground`，语气由圆点、边框和淡底表达（第 4 节）。
- 浮层遮罩没有专用令牌：浅色 `bg-foreground/40`，深色 `dark:bg-background/70`。
- 画布库读不了 CSS 变量：`web/src/lib/charts/theme.ts` 的 `readChartTheme()` 在挂载与主题切换时把上表解析成十六进制（见第 8 节）。

## 3. 布局

- **应用壳**：左侧栏（可折叠，折叠后只剩图标 + 工具提示）、顶栏（当前页标题、事件提示、主题切换）、内容区 `max-w-[1400px]`，表格页允许撑满。侧栏与顶栏是 `AppShell` 唯一的职责。
- **页头**（每个路由与每个大段落一致）：标题 + 口径 / 时点 / 版本的一行次要文字 + 右侧动作区（**至多一个主按钮**，其余描边或链接）。用 `PageHeader`。
- **分段**：`Section`（标题 + 可选说明 + 内容），不嵌套超过两层；卡片只给「一个对象」（一份计划、一张复盘卡、一个数据族），列表性内容用表格不用卡片堆。
- **网格**：桌面 ≥ 1024px 两栏或四栏；768–1023 两栏；< 768 单栏。
- **窄屏（< 768px）**：「今日」计划表、计划页 `#plan/<id>`、运行详情 `#tools/runs/<id>`，以及账户页的持仓（含已平仓批次）、成交与模拟订单表以**行转卡**呈现（每行变一张紧凑卡，字段名在左，`DataTable` 的 `cards="narrow"`）；其余页面表格横向滚动、侧栏变顶部抽屉。不做 < 375px。判断窄屏用 `useNarrow()`（`web/src/lib/ui/narrow.svelte.ts`，查询串 `(max-width: 767px)`）；没有 `matchMedia`（jsdom、旧浏览器）时按桌面。

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

每个域对应后端的一个状态来源；`tests/test_frontend_design_guards.py::test_status_vocabulary_complete` 保证表里不缺后端能产生的值。同名键按域区分：`node` / `run` 的 `pending`（未开始）是 `neutral`，`execution.pending`（入场期，未成交）是 `info`，`connection.pending`（登录中）是 `running`。

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

### 合并实施的五组中文（v2.12–v2.15 词表）

下列常量在 `web/src/lib/` 的前端常量里逐字出现；后端 `core.texts` 与跨端 sync 测试须保持一致，修改时三处一起改：

```ts
export const RESEARCH_STATUS_TEXT = { queued: '排队中', running: '研究中', waiting_data: '等待数据', concluded: '已得结论', closed: '已结束', error: '出错' } as const
export const PROPOSAL_STATUS_TEXT = { proposed: '待决定', approved: '已批准', rejected: '已驳回', superseded: '已取代' } as const
export const PORTFOLIO_VERDICT_TEXT = { ok: '可执行', blocked: '被拦截', deferred: '暂缓' } as const
export const DATA_REQUEST_STATUS_TEXT = { queued: '排队中', fetched: '已补采', failed: '失败', deferred: '暂缓' } as const
export const FEATURE_FLAG_TEXT = { enabled: '已开启', disabled: '已关闭' } as const
```

research_request 展示域的 error 只用于结论版本；请求 SQL 集合仍只有 queued/running/waiting_data/concluded/closed，不得放宽为六态。路线版本另为 planned「已规划」、in_progress「实施中」、engineering_done「工程完成」、user_acceptance_pending「待用户验收」、done「已完成」、dropped「已放弃」。approved「已批准」不代表已上线或已验收。

组合五硬拒绝码逐字：sector_cap「超过行业集中度上限」、max_per_sector「超过同行业标的数量上限」、single_cap「超过单笔风险或单票敞口上限」、total_cap「超过组合总敞口上限」、held_ticker「已持有、在挂或重复的标的」。sector_unknown「行业未归类」、deferred_by_cash「现金不足，暂缓执行」仅提示 / 暂缓，不进硬拒绝表。

与 spec §4.4 表的差异（D16）：表里写的 `green` / `yellow` / `red`、`done`、`gate_hold` 等在后端没有对应枚举（数据族卡显示的是拉取记录状态，即 `fetch` 域），不进词汇表；旧 `StatusPill` 的这几个键无调用方。

`chain.invalidated`（已作废）在表里是 `neutral`，不是 spec 表里「作废 → danger」的字面读法：它是链的终态（`ENDED_STATES`），整行本来就置灰（`muted`），属于「已结束」一类，与 `expired`、`position_closed` 同列；`danger` 留给真正的异常（失败、出错、校验未通过、需要重新登录）。

### 维护 `status.ts`

1. **后端多了一个状态值**：先在 `TONES` 对应域里加一行键（**每个域保持单行** `domain: { key: 'tone', … },`，键可裸写或用单引号，不用展开运算符——Python 守护按行解析），再在 `LABELS` 里补中文，然后才在页面里用。已有中文表的域（`node`、`run`、`retro`、`push`、`chain`、`card`、`knowledge`、`instruction`）的中文**引用现有常量**（`NODE_STATUS_TEXT`、`RUN_STATUS_TEXT`、`RETRO_STATUS_TEXT`、`RETRO_PUSH_TEXT`、`CHAIN_STATE_TEXT`、`CARD_STATUS_TEXT`、`KNOWLEDGE_STATUS_TEXT`、`INSTRUCTION_TEXT`），常量缺键时去补常量及其跨语言测试，不在 `status.ts` 里另抄文字。
2. **新域**：加进 `StatusDomain` 联合、`TONES`、`LABELS` 三处；`status.test.ts` 会检查每个键都有中文、已有常量按引用复用。
3. **带占位符的文案**（`EXECUTION_TEXT.adopted` 是「已采纳（{date} 成交 {quantity} 股）」）：`StatusPill` 的 `text` 传填好的句子（`rowExecutionText` / `executionText`），语气仍取自表。
4. `node` 域同理：`NODE_STATUS_TEXT`（`runs.ts`，不改）没有运行一览才有的 `missing` / `not_recorded`，所以 `LABELS.node = { ...NODE_STATUS_TEXT, missing: '缺证据', not_recorded: '未记录' }`，`status.test.ts` 检查它包含 `NODE_STATUS_TEXT` 的每一项。
5. `execution` 域是「复用但不是同一个对象」的例外：`EXECUTION_TEXT` 没有 `unplanned`（周复盘 `by_execution` 的键，文案「计划外成交」由 `executionText` 单独给出），所以 `LABELS.execution = { ...EXECUTION_TEXT, unplanned: '计划外成交' }`。
6. 开发模式未知键会抛 `unknown status: <域>.<键>`，这是有意的：新状态必须先进表。生产环境不抛，显示原键 + 中性灰。

## 5. 数据块五态

每个取数的块（页面、段、卡、表）用同一个 `DataBlock` 包装，必选 `state`：

| 态 | 显示 | 文案规则 |
| --- | --- | --- |
| `loading` | `Skeleton`，形状接近最终内容（`skeleton`：`row` 一行 / `block` 一块 / `table` 表头加五行） | 不写字（容器带 `aria-busy` 与 `aria-label="加载中"`） |
| `empty` | 一行次要文字 | 写**为什么空**：「无持仓」「今日无触发」「暂无成交」——是业务上的「无信号」 |
| `not_computed` | 一行次要文字 + `warn` 徽标「没算」 | 写**缺什么**：「缺资金快照，未计算股数」「样本 3 个，少于 5 个不给均值」——与 `empty` 严格区分（roadmap §2 原则 3） |
| `degraded` | 内容照常显示 + 顶部 `Alert`（warn） | 写**哪部分不全**与原因：「Yahoo 空结果，日线用上一交易日」「概念卡暂不可用：<原因>」 |
| `error` | `Alert`（danger）+ 重试按钮（若可重试） | 原因来自接口 `{"error": "<代码>"}` 的中文映射（沿用各 `*_TEXT` 表），不显示原始英文码与堆栈 |

另有 `ready`：只渲染内容。页面级错误（整页取不到）用同一组件撑满内容区（`page`，只对 `loading` / `empty` / `not_computed` / `error` 生效并居中；`ready` / `degraded` 照常满宽渲染内容）；块级错误只占该块。**调用方必须给 `empty` / `not_computed` 传 `message`**（写为什么空、缺什么）；组件内的默认文案（`empty` 「暂无数据」、`degraded` 「部分数据不全」、`error` 「加载失败」）只是兜底，页面上出现兜底文案就是漏写。**禁止**用空白、`—`、`null`、`undefined` 表达以上任一态。`MetricValue`、`CutoffTime`、`SampleSize`、`PlanLevels` 缺值时自带「没算」徽标或文字，不透传 `fmtBeijing` / `fmtNum` 返回的「—」。

**存量例外**：被现有测试钉住期望文本、按 spec §8.1 保留「—」的位置（运行一览「输入 — · 产出 —」、数据页 FRED 任务「下次 —」、`PlanTimeline` 的「执行 —」「影子 —」、复盘「关联计划：—」与「n=0 · 均 — · 中 — · 正 —」、自由现金流、`DataBrowse` 原始值单元格等），以及页面层约 76 处直接调用 `fmtBeijing` / `wb.pct` / `fmtPct` / `wb.dateTime` / `signedPct` / `rText` 的地方（字段多数非空，未逐一迁移）。**新代码不得照抄**：缺值一律走 `DataBlock` / `MetricValue` / `CutoffTime` 或写状态文字（「未记录」「无」「未提供」「无分组」……）。

## 6. 组件清单

通用层在 `web/src/lib/ui/<组件>/`（shadcn-svelte 1.7.2 复制来的，样式已按第 2 节改写；Bits UI 2.20.2），领域层在 `web/src/lib/ui/domain/`（自写）。页面**只组合这两层的原语**，不自己拼样式。

### 6.1 通用层

导入路径形如 `import { Button } from '$lib/ui/button/index'`。

| 组件 | 路径与 API | 规则 |
| --- | --- | --- |
| `Button` | `ui/button/`：`variant`：`primary` / `outline`（默认）/ `ghost` / `link` / `destructive`；`size`：`sm`（28px，表格内）/ `md`（32px，默认）/ `icon`（28px 方形：主题切换、关闭、侧栏折叠）；`busy`（禁用 + `aria-busy` + 转圈）、`busyText`（忙时替换文案）；传 `href` 渲染成 `<a>` | 一个页头或一段至多一个 `primary`；导航用 `<a href>`，动作类的下划线按钮用 `variant="link"`；`link` 的 `sm` / `md` 都是 `h-auto px-0`，可内嵌在文字里 |
| `Badge` | `ui/badge/`：无 `variant`，只有中性底；传 `href` 渲染成 `<a>` | 不直接用；由 `StatusPill` 加语气 |
| `DataTable`（`Table`） | `ui/data-table/`：`columns: Column<T>[]`（`key`、`header`、`align: 'left' \| 'num' \| 'center'`、`title` 表头提示、`cell` 自定义单元格 snippet、`value` 纯文本取值、`cardLabel`、`hideInCards`）；`rows` 或 `groups: RowGroup<T>[]`（`key`、`label`、`rows`、`muted`、`collapsible`、`open` 初始状态、`onToggle`）；`rowKey`；`caption`；`headerGroups`（分组表头）；`expandable` snippet + `bind:expanded`；`muted(row)`；`cards: 'never' \| 'narrow'`；`ariaLabel`（给 `<table>` 和卡片列表一个可访问名称，不出现可见 caption）；`expandLabel(row, open)`（行展开按钮的可访问名称，默认「展开详情」，例如 `展开 MU` / `收起 MU`）；`pageSize`（默认 50，「显示更多」）；`sticky`（默认 true）。底层 `ui/table/` 的 `Table.Root` 有 `containerClass` | 数字列 `align: 'num'`；状态列用 `StatusPill`；已结束 / 作废 / 被取代的行置灰（`muted`），不删不隐藏；展开按钮可用 `Enter`，名称恒为「展开详情」、状态在 `aria-expanded`；空表接 `DataBlock` |
| `ConfirmDialog`（`Dialog`） | `ui/confirm-dialog/ConfirmDialog.svelte`：`bind:open`、`title`、`consequence`（后果一句话）、`actionLabel`（动作名，不写「确定」）、`destructive`、`busy`、`onConfirm`、`onCancel`、`confirmDisabled`（只禁用动作按钮，如必填项还没填）、`children`（可选的表单体，渲染在说明与按钮之间，如作废理由） | 不可逆动作必经；**确认后不自动关**，调用方做完事再 `open = false`；`busy` 时 `Esc` / 遮罩 / 取消都不响应；初始焦点在「取消」（`onOpenAutoFocus` 显式落到取消按钮，有表单体时也一样；`busy` 时两个按钮都禁用，交给 Bits 默认处理），关闭后焦点回到打开前的元素；底层 `ui/dialog/` 内容渲染在 Portal |
| `Sheet` | `ui/sheet/`：`SheetContent` 的 `side`、`width: 'sm' \| 'lg'`（24rem / 40rem，窄屏全宽，默认 `sm`） | 右侧抽屉 |
| `Tabs` | `ui/tabs/`（Bits `Tabs`） | 只用于**不改地址**的页内页签；方向键 / `Home` / `End` 由 Bits 负责。本版除画廊外没有页面使用它（运行详情三块仍是纵排 `Section`），保留为备用原语 |
| `TabNav` | `ui/tab-nav/TabNav.svelte`：`label`、`items: { href, label, current, badge?, badgeLabel? }[]`；`badgeLabel` 写成角标的 `aria-label` 与 `title`（如「待审定 2 张」），没给就保持原样 | 路由级页签（「复盘」「知识」「工具」「设置」的子页）是**导航**：链接 + `aria-current="page"`，不是 `tablist`（D12）。已知取舍：角标的 `aria-label` 挂在无角色的 `<span>` 上（为保持旧测试 `getByLabelText('待审定 N 张')`），非 Chrome 的读屏器不一定读出，所以角标的数字旁仍应有可见文字 |
| `Field` | `ui/field/Field.svelte`：`label`、`id`、`hint`、`error`、`required`；children 得到 `{ id, describedby, invalid }` | 标签在上，说明与错误在下（错误 `role="alert"`，输入框 `aria-describedby`）；必填 `*` 画在标签旁、不在 `<label>` 内；数字字段 `inputmode="decimal"` 右对齐 |
| `Input` / `Textarea` / `Select` / `Label` | `ui/input/`、`ui/textarea/`、`ui/select/`、`ui/label/`；`Select.Trigger` 的 `size`：`sm` / `md` | 放进 `Field` 使用。**原生 `<select>` 与 `<details>` 是允许的写法**：现有测试以 `change` 事件驱动 `<select>`（本版 13 个文件共 20 处，各自写类串），原始数据折叠用原生 `<details>`（21 处，未经 `Collapsible`）；新页面优先用 `Select` / `Collapsible`，改动这些旧处时顺手迁移，不为迁移单独改 |
| `Tooltip` | `ui/tooltip/`；`Tooltip.Provider` 在 `app.svelte` 全局提供，默认延迟 300ms | 侧栏折叠时的图标标签；表头缩写说明 |
| `Popover` | `ui/popover/` | `ConceptTerm` 的就地展开（同一页同时只开一个、`Esc` 收起的规则沿用） |
| `Collapsible` | `ui/collapsible/` | 规则基线折叠、已结束组、「看推理过程」、侧栏子菜单。**用法**：`CollapsibleContent` 外面再包 `{#if open}` 时加 `forceMount`（让 `{#if}` 当开关）；Bits 的内容在 jsdom 里挂载即展开时会一直 `hidden`（SideNav 复现，最小例子复现不了，见 `collapsible.test.ts`）。关闭态的内容留在 DOM 里带 `hidden` |
| `Alert` | `ui/alert/`：`variant`：`info`（默认，`role="status"`）/ `warn` / `danger`（`role="alert"`）；`role` 可覆盖；配 `AlertTitle`、`AlertDescription`、`AlertAction` | 取代各处 `role="alert"` 的 `<p>` |
| `Skeleton` | `ui/skeleton/`：一个 `animate-pulse` 的块，用 `class` 定尺寸；行 / 块 / 表三种预设在 `DataBlock` 里 | |
| `Toast` | `ui/toast.ts`：`notify.success(msg)` / `notify.info(msg)`（3 秒自动消失）、`notify.error(msg)`（不自动消失，带「关闭」按钮）；`Toaster` 在 `app.svelte` 挂载（`ui/sonner/`，主题读 `lib/theme.ts`） | 操作结果；没有 `matchMedia` 时 `Toaster` 不渲染，页面测试里改为断言对 `notify` 的调用 |
| `Separator`、`Card` | `ui/separator/`、`ui/card/`（`Card.Root`、`Header`、`Title`、`Description`、`Action`、`Content`、`Footer`） | 布局用；卡片只给一个对象 |

### 6.2 领域层

导入路径形如 `import StatusPill from '$lib/ui/domain/StatusPill.svelte'`。

| 组件 | props | 职责 |
| --- | --- | --- |
| `StatusPill` | `{ domain: StatusDomain; value: string; text?: string; title?: string }` | 按第 4 节词汇表显示状态：圆点 + 文字，`running` 语气带 `animate-running`；`text` 覆盖文字（填好占位符的模板），语气不变；`title` 写原因。DOM 带 `data-domain` / `data-status` / `data-tone` |
| `VersionChip` | `{ version: number \| string; latest?: number \| null; href?: string; latestHref?: string; ref?: string }` | 数字版本保持 `第 k 版` 与最新版本链接；合并实施扩展字符串版本用于账户 agent_version，显示真实版本标识；`ref`（`concept:<id>@<v>` / `know:<id>@<v>`）作原生 `title`，同时放 `data-ref` |
| `CutoffTime` | `{ at: string \| null \| undefined; label?: string }`（默认「截止」） | `{label} {fmtBeijing(at)}`，后缀是 `fmtBeijing` 的「北京」（D17，`lib/format.ts` 不改）；`<time datetime>` 的 `title` 是相对时间（「3 小时前」「45 分钟前」「2 天前」「刚刚」）；`at` 为空显示 `StatusPill block.not_computed`，不透传「—」；无法解析的值原样显示、不带提示 |
| `SampleSize` | `{ n: number \| null; min?: number }`（默认 5） | `n = {n}`；`n < min`（含 0）加 `block.insufficient`；`null` 显示 `block.not_computed`（没算） |
| `CalcVersion` | `{ version: string; concept?: string }` | `口径 ` + 等宽代码样式的版本号；有 `concept` 时链接到 `#knowledge/<concept>` |
| `MetricValue` | `{ value: number \| null \| undefined; raw?: string \| null; unit?: string; digits?: number; signed?: boolean; percent?: boolean; tone?: 'updown' \| 'none' }`（`digits` 默认 2，`tone` 默认 `none`） | 等宽数字（`tabular-nums`），单位前加半角空格（`12 股`），`percent` 的值是小数（0.032 → `3.2%`，与 `fmtPct` 一致，无空格），`signed` 给正数加 `+`；`updown` 时正 `text-up`、负 `text-down`、零不着色；`-0.00` 归一成 `0.00`；`null` / `undefined` / `NaN` 显示 `block.not_computed`；传非空 `raw` 时优先显示原文，不解析或舍入后端 Decimal 字符串，`unit` 仍保留，长值可换行 |
| `PlanLevels` | `{ low: number \| null; high: number \| null; stop: number \| null; target1: number \| null; shares?: number \| null; lead?: string; sharesLabel?: string }` | 一行标准写法 `入场 95.10–97.30 · 止损 91.40 · T1 104.70`，有股数时加 ` · 建议 120 股`；`lead`（默认「入场」）换掉区间前的词，`sharesLabel`（默认「建议」，空字符串则只写 `120 股`）换掉股数前的词——`PlanTable` 窄屏价位行传入场类型文字和空 `sharesLabel`，保持旧写法 `区间限价 95.10–97.30 · 止损 91.40 · T1 104.70 · 120 股`（价格两位小数，区间用 U+2013）；props 是数字，而接口里的价位是字符串（`"95.10"`）：**调用方用 `Number()` 转换**再传（接口不变；无法转成数字的值按缺失处理）。缺的价位写「没算」，四个都缺时是一个 `block.not_computed` 徽标。格式取自现有 `PlanHistoryTable` / `PlanTimeline` / `RuleBaseline` / `PlanRowDetail` 的写法，`PlanLevels.test.ts` 钉住的是数字格式（两位小数、U+2013、缺值写「没算」）和 `lead` / `sharesLabel` 的默认值 |
| `RefLink` | `{ ref: string; onFact?: (key) => void; onCall?: (ref) => void; expanded?: boolean }` | `RefList` 单项的渲染：`fact:` 有 `onFact` 时是调用回调的链接式按钮，否则纯文字；`call:` 有 `onCall`（调用方能展开）时是 `aria-expanded` 按钮，否则纯文字；`know:` 链到 `knowHref`；`concept:` 用 `ConceptTerm` 就地展开；其余（`user:` 等）纯文字。列表外壳与展开的工具调用区域仍归调用方（`RefList`） |
| `SourceBadge` | `{ source: string; fallback?: boolean }` | 数据来源名（等宽）；`fallback` 时加淡警告底与文字「备源」。合并实施用于数据来源及研究事实种类，不把推断显示为披露事实 |
| `DataBlock` | `{ state; message?; skeleton?: 'row' \| 'block' \| 'table'; onRetry?; retryLabel?: string; page?: boolean; children? }` | 第 5 节五态包装；`degraded` 先 `Alert` 再渲染 `children`，`error` 有 `onRetry` 时加一个按钮（默认「重试」），`page` 撑满内容区（`min-h-[50vh]`） |
| `PageHeader` | `{ title: string; level?: 1 \| 2 \| 3; meta?: Snippet; actions?: Snippet }`（`level` 默认 2） | 标题 + `meta` 次要行 + 右侧一个动作区（`data-slot="page-header-actions"`）。**迁移时每个标题保持原有级别**（测试按 `level` 查标题） |
| `Section` | `{ title?: string; level?: 2 \| 3 \| 4; description?: string; actions?: Snippet; children: Snippet }`（`level` 默认 3） | 分段；有标题时是 `aria-labelledby` 命名的 `region`；级别规则同上 |
| `Icon` | `{ name: IconName; size?: 16 \| 20; label?: string }` | `@lucide/svelte` 的薄包装；无 `label` 时 `aria-hidden="true"`，有则 `role="img"` + `aria-label`。`icons.ts` 是显式映射表（逐个 `import X from '@lucide/svelte/icons/<name>'`），**加图标就是在这张表加一行** |

`icons.ts` 现有图标：`calendar-days`、`search`、`clipboard-list`、`database`、`book-open`、`wrench`、`settings`、`activity`、`bell`、`sun-moon`、`x`、`check`、`chevron-down`、`chevron-right`、`info`、`external-link`、`refresh-cw`、`triangle-alert`、`circle-x`、`loader`、`menu`、`panel-left-close`、`panel-left-open`。

### 6.3 已知取舍

- **`DataTable` 宽表在容器内滚动，放得下才让表头吸顶。** 容器 `overflow` 一旦不是 `visible`，`sticky` 就只在容器内生效、贴不到视口顶部；而容器是 `visible` 时，比可用宽度还宽的表会经由一串 grid 祖先（`min-width: auto`）把整页撑出横向滚动（v2.10 验收在「今日」计划表 1024–1280px 下复现）。所以 `sticky`（默认）的表用 `ResizeObserver` 和窗口 `resize` 测量：先临时把容器设成 `overflow-x: auto`，看表格宽度是否不超过容器；放得下时容器为 `max-lg:overflow-x-auto lg:overflow-visible`（≥ 1024px 表头吸顶），放不下时一律 `overflow-x-auto`（在容器内横向滚动，表头不吸顶）。`<table>` 上的 `data-fits` 记录测量结果；测量前、没有 `ResizeObserver`（jsdom）时和 `sticky={false}` 的表都按放不下处理，在任何宽度都在容器内滚动。放在 `Card` 里的表即使放得下也在卡片内滚动（`lg:in-data-[slot=card]:overflow-x-auto`）：`Card` 自带 `overflow-hidden`（圆角裁切），粘性表头在卡片里本来就贴不到视口。
- **`RefLink` 依赖 `lib/components/ConceptTerm.svelte`**（`ui/` 反向依赖了 `components/`，分层倒置，本版接受）。`ConceptTerm` 已迁到 `Popover`，但仍留在 `lib/components/`；日后搬进 `ui/domain/` 时 `RefLink` 的 import 随之改路径。`RefLink` 的动作类引用（`fact:` / `call:`）是 `Button variant="link"`，用 `text-[length:inherit]` 继承所在文字的字号并允许选中文字；导航类（`know:` / `concept:`）是 `<a href>` 或 `ConceptTerm`。
- **`StatusPill` 的文字不着状态色**，原因见第 2 节要点：淡底上的同色文字在浅色主题下对比度不足 AA。
- **卡片视图里的长状态标签换行。** `Badge` 默认不换行（表格列宽按内容算，允许换行会把中文标签挤成一字一行）；`DataTable` 的 `cards="narrow"` 卡片里，徽标改为可换行、最宽到单元格宽度、圆角 10px（单行时仍是胶囊形），所以「Agent 未给出计划：模型调用失败，以下为规则基线」这样的长标签在 375px 下折成两行，不撑出屏幕。其他窄屏场景里放长标签，要自己给容器留宽度或换写法。
- **`CutoffTime` 的相对时间用原生 `title`**，不用 `Tooltip`（不依赖 Provider，任何地方可用）。`VersionChip` 的 `ref` 同理。

### 6.4 研究、路线与组合页面的组合方式

沿用 PageHeader、Section、DataTable、DataBlock、StatusPill、CutoffTime、CalcVersion、ConfirmDialog、Card，不另建页面样式体系：

- `Research.svelte` / `research/DossierList`、RequestForm、DossierVersion、Facts：列表展示问题、状态、截止与预算；提交前 ConfirmDialog 提示额度；版本导航用 TabNav，每版按「论点 / 事实与来源 / 催化 / 反证 / 价格与情景 / 下一步」分 Section。Facts 用 SourceBadge 分 disclosure/guidance/price_fact/user/inference，blocked 单列；未记录的催化 / 下一步写「本版未记录」。AgentReasoning 懒加载，公司研究在提议者前，原 call:N 可跳转，不重新编号。切换请求时清空旧关闭对话框，不将旧异步结果画到新请求。
- `Roadmap.svelte` / `roadmap/RouteTab`、ProposalsTab、DecisionsTab、EvidenceTab：四个导航页签，列表 DataTable、详情 Section，批准 / 驳回用 ProposalDecide + ConfirmDialog 填理由，批准另填 version_id；版本和决策展示真实最新行与全部 revision（含当前）。evidence_rows 只展开生成时原 body；空包说明「历史证据包缺少该引用的记录」。
- `roadmap/Brief.svelte` 共用版本页与提案页，not_requested/pending/ok/error 分开；retry 返回 pending 后必须 GET 读回，pending 可「刷新 brief」、error 可重试。提交任务不显示成生成成功，不拼假的历史行或 brief。
- `settings/FeatureFlags.svelte` 读真实 flags/history；双向理由必填、关闭确认，POST 成功后 GET 读回两者，空历史才显示「尚无变更记录」。
- `today/PortfolioCheck.svelte` 与 `account/PortfolioResult.svelte`：硬结果按 order，经理 ranking 另列，行业敞口用语义 meter、互斥用表。K2 关闭整个节点为 not_computed；开启且硬结果已存后 PM 失败才用 DataBlock degraded 显示「只有硬结果」。缺行不能当 empty/ready 的空组合。
- RunDetail 的 portfolio 按账户计数展开真实结果，proposals 链到路线页，agent 显示五项补查计数与数字研究请求数；节点列表由后端版本决定，不给旧运行补两节点。

路径均相对 `web/src/lib/components/`（两个路由在 `web/src/routes/`）。人工目检记录在 [验收清单](roadmap/v2.12-v2.15-acceptance.md)，尚未执行。

## 7. 交互规范

- **动作**：一个页头或一段至多一个 `primary` 按钮；不可逆动作（续跑、重新运行 Agent、作废成交、断开连接、归档知识、撤回补充）必须经 `ConfirmDialog`，确认按钮写动作名不写「确定」。不使用原生 `confirm` / `alert` / `prompt`（唯一豁免：`lib/navigation-guard.ts` 的未保存编辑离开确认，它依赖同步返回值，行为不变）。
- **提交与幂等**（沿用 `request_id` 规则）：提交中按钮 `busy`（并在处理函数里加重入保护，进行中再点不重复提交）、表单只读；失败保留草稿与 `request_id` 并在表单内 `Alert` 显示原因；成功后 `notify.success`。
- **轮询**（重新复查、讨论回答、登录、续跑）：进行中显示 `running` 徽标与已等待时间；超时文案沿用各自的 `*_PENDING_TEXT`，不自动无限重试。
- **链接与按钮**：导航用 `<a href="#…">`（可中键打开、可复制），动作用 `<button>`（即 `Button`）；二者视觉不同（链接下划线，按钮无下划线）。
- **键盘**：所有可交互元素 `focus-visible` 显示 `--ring` 焦点圈；`Esc` 关闭最上层浮层；表格行展开可用 `Enter`；侧栏可 `Tab` 遍历。由页面自己打开的 `Sheet` / 对话框（不是 `Dialog.Trigger`）在打开时记下 `document.activeElement`，并在 `onCloseAutoFocus` 里把焦点还给它（复盘的工具调用抽屉、`FetchDateModal`）；`ConfirmDialog` 自带初始焦点（取消）与关闭后回到打开前元素。
- **表单**：标签在上；错误在字段下且 `aria-describedby`；数字字段 `inputmode="decimal"`、右对齐；日期时间字段注明时区。
- **表格**：数字列右对齐等宽；文本列左对齐；状态列用 `StatusPill`；表头可带工具提示；超过 50 行的表分页或「显示更多」（接口已有 `total`）；已结束 / 作废 / 被取代的行置灰（`muted`），不删不隐藏。
- **深浅色**：切换即时生效（图表重绘），所有颜色经令牌；`lib/theme.ts` 的 `getTheme()` / `toggleTheme()` / `onThemeChange()`。
- **安全**：不新增 `{@html}`（现状只在知识卡页的两处：概念卡正文、原理分节正文），迁移时不得为了样式引入。
- **文案**：错误原因一律中文映射，不显示英文码；相对时间只作工具提示，正文写绝对时间。

## 8. 图标与图表

- 图标只用 `@lucide/svelte`，通过 `Icon` 组件；导航图标：今日 `calendar-days`、标的 `search`、复盘 `clipboard-list`、数据 `database`、知识 `book-open`、工具 `wrench`、设置 `settings`、运行 `activity`、通知 `bell`、主题 `sun-moon`。不再出现 emoji（守护检查）。
- ECharts：`web/src/lib/charts/theme.ts` 的 `readChartTheme()` 在挂载与主题切换时读取 `--foreground`、`--muted-foreground`、`--border`、`--chart-*` 生成十六进制的主题对象；`EChart.svelte` 以该对象初始化，主题切换时销毁重建并重新 `setOption`。系列色按来源角色（规则 / 自定 / Agent）而不是序号。
- K 线：涨跌用 `--up` / `--down`；标记与价位线按 `--chart-rule` / `--chart-own` / `--chart-agent`；作废计划用 `--chart-fill` 淡显；代码里不再写十六进制。候选标记（买入 / 卖出箭头、观察圆点）由规则引擎产生，标记本身不带计划来源，统一用规则色 `--chart-rule`，方向靠形状；成交标记 B / S 用 `--up` / `--down`；价位线按计划来源三色。lightweight-charts 的线条描边直接认 `#rrggbbaa`，所以 `--chart-fill` 的透明度对作废计划的价位线有效（只有它的辅助标签路径会丢 alpha，`CandleChart` 不走那条路）。`candleOptions(theme)` 返回 `{ chart, series }`：`chart` 给 `createChart` / `applyOptions`，`series` 给 `addCandlestickSeries`；图表背景用 `card`。
- 图表旁必有文字说明口径与时点（`CalcVersion` + `CutoffTime`）。

### 账户分界 markers（合并实施）

compare 与 equity 响应共用 `markers:[{session,kind,label}]`。ECharts 以交易日作 xAxis 的 markLine，颜色仍取主题；feature「功能开关」为 dashed 虚线、version「版本」为 solid 实线、params「参数」为 dotted 点线，不能只靠颜色区别。来源分别为真实 feature_flag_changes、paper_decisions 首次新 agent_version 的 session、account_param_changes.effective_from。

`account/Markers.svelte` 在曲线下用 Section「分界」和 DataTable 列「交易日 / 种类 / 分界说明」，按 session 排序。markers 缺字段为 not_computed「接口尚未提供分界记录」；真实空列表为 empty「本区间没有功能、版本或参数分界」。两者不混淆，不从曲线形状猜变更。导航新增 `microscope`（研究）、`map`（路线），走现有 Icon 映射。

## 9. 文案

沿用现状并写进标准：全角标点，「」作引号，中西文与数字之间半角空格，单位紧跟数字（`12 股`、`-3.2%`），时间 `fmtBeijing`（输出末尾带「北京」），版本 `第 k 版`，口径名用等宽字体（`CodeText` / `CalcVersion`）。与后端逐字对齐的文案常量（`AGENT_REASON_TEXT`、`RESUME_ERROR_TEXT`、`conditionText`、`EXECUTION_TEXT` 等）**一字不改**，仍由现有跨语言测试守护。

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
