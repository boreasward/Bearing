# Agent 轨道

每个买入单元在规则主计划旁并列一条 Agent 建议；卖出、观察和有冲突的单元，由 Agent 生成只读解读。Agent 是**挑战者**：只做影子跟踪、并列展示，不改主计划。

设计：[P3 Agent 轨道](../specs/2026-10-24-p3-agent-track-design.md)、[P4 复盘扩展](../specs/2026-10-25-p4-review-expansion-design.md)（解读并入 Agent 轨道）、[v2.5 计划时间线](../specs/2026-10-28-v2.5-plan-timeline-design.md)（`agent-2` 结构与版本链）。

## 纯校验准备（五层发布 Task3）

已加入 `research_check` 与 `pm_check`，当前模型图尚未激活研究员 / PM 阶段。研究事实须 1–12 条、推断最多三条且引用本段非推断事实所用的有效 `call:N`；研究摘要、风险、blocked 结构分别校验，事实块保留原调用编号。PM 只能排序宿主 `ok` 计划，不得写价位 / 股数，引用只认本次调用与 `fact:portfolio_check`。

标准指标已有 22 项注册：close 与 adj_close 三组均线分别登记，Agent 相对强弱用 SPY、筛选用 QQQ，既有工具 dict 的键序 / 数值不变；独立推导仍不调注册表计算。优化层已有证据汇总、提案 / brief 校验与 Markdown 导出纯函数，但尚无提案节点激活。counter 的可观测比例仅为「带反方意见运行后来出现成功修订后继」的比例，不证明逐条意见采纳或因果关系；提案引用必须落在生成时的证据行 ID，坏条目单独丢弃。

## 怎么工作

- **运行时**：LangGraph，模型走你的 ChatGPT 订阅（v2.8，Sign in with ChatGPT，见下面「模型层」），检查点存在本库（PostgresSaver）。
- **工具层**（`analysis/agent/tools.py`）：十个普通 Python 函数（批处理的提议、反方、复查、解读和复盘卡起草都用这十个）。前八个（`get_trigger`、`get_price_bars`、`get_indicators`、`get_filings`、`get_news`、`get_earnings`、`get_market_context`、`get_position`）只能读运行截止时间之前的数据，只允许查单元自身的标的和 SPY/SMH/XLK/QQQ，不接受日期参数。截止时间由批处理传入，模型无法修改。每次调用的参数、返回值和 SHA-256 都写入 `agent_tool_calls`。调用由 `Recorder` 按先后从 1 起连续编号，编号就是模型可引用的 `call:N`（`agent_tool_calls.seq`，`(run_id, seq)` 唯一）；模型在一轮里并行发出的多个工具调用会被 LangGraph 的 `ToolNode` 放到线程池同时执行，编号与追加记录在同一把锁内完成，所以并行调用也各得一个号、不重不漏。
- **`get_concept`**（v2.6，第九个工具）：`get_concept(concept_id=None)` 读取系统概念卡（[知识层](knowledge.md)）：不给 id 返回全部 id、标题与分组，给 id（如 `plan.stop`）返回整张卡（`concept_ref`、版本、`summary`、给 Agent 的纯文本 `body`、`content_sha256`）。它是**截止时间的唯一例外**：不读数据库、不用截止时间，概念卡随代码版本走，任何时刻读到的都是当前代码版本的卡；结果里写明 `point_in_time: false` 和说明，连同 `concept_ref`、`content_sha256` 原样存进 `agent_tool_calls`，推理阅读视图对这类调用标「概念卡随代码版本走，不受截止时间约束」。未知 id 记成错误信封「未知概念 <id>，不给 id 可取全部列表」；卡目录校验失败记成「概念卡不可用：<第一条问题>」，只影响这个工具。它不是价格类工具（价位仍须引用 `get_price_bars` / `get_indicators`），它的 `call:N` 与其它工具一样可以被论点引用；提示词、`agent_version`、工具轮数上限与预算都不变。
- **`get_knowledge`**（v2.7，第十个工具，排在 `get_concept` 之后）：`get_knowledge(ticker=None, concept=None)` 读你审定过的经验（[我的知识](knowledge.md)，只读条目、版本与状态三张表，**没有任何工具读复盘卡草稿或教训草稿**）。给 `ticker`（过同样的标的范围检查）或 `concept`（`plan.stop` 或 `concept:plan.stop@1`，版本忽略）取挂在上面的条目，两个都给 → 错误信封「ticker 与 concept 只能给一个」，未知概念 → 「未知概念 <id>」；都不给返回索引（`ref`、标题、挂靠、`written_at`，不含正文，至多 50 条）。条目按 `written_at` 倒序至多 10 条，超出记 `omitted`；每条带 `ref`（`know:<id>@<版本>`）、`id`、`version`、标题、正文、适用范围、反例、挂靠。**按截止时间读**：只含 `created_at ≤ cutoff` 的版本，截止时间时已归档的不返回，之后写的新版本、之后的归档都看不到；结果写明 `point_in_time: true`、`cutoff_at` 和说明「这是用户审定过的经验，是判断不是事实；价位不能只引用它」，推理阅读视图对这类调用标「我的知识按截止时间读取：只含截止时间之前写入的版本」。迁移 027 未执行 → 错误信封「我的知识不可用：迁移 027 未执行」，只影响这个工具。它不是价格类工具。
- **`know:` 引用与 `knowledge_citations`**（v2.7，`agent-2` / `agent-review-1` 不升版本）：论点、失效条件、变化说明、反证、风险和复查结论的引用里可以写 `know:<id>@<版本>`，**只能引用本次运行里 `get_knowledge` 返回过的版本**（`Recorder.knowledge_refs`，其余校验器不改，只是调用方传入的允许集合变大）；价位仍须引用价格类工具，所以经验不能单独支撑价位。提议、反方、复查三份提示词各加一段说明（`graph.KNOWLEDGE_NOTE`）。计划通过校验后，`growth_check.knowledge_citations` 从**保留下来的**字段抽出引用（`thesis`、`invalidation`、`changes`、`counter`、`risks`、`review`，价位的引用不记；被反方丢弃的条目不记），首版在 `_run_agent_unit` 写计划行的事务里、修订在写新版的事务里（带新版 `plan_id`）、复查在写 `plan_reviews` 行的事务里（带 `review_id`、`plan_id` 为空，维持 / 作废 / 修订结论都记）写进 `knowledge_citations`，每行带返回它的 `call_seq`；`pass`、作废、出错的运行不写。计划写入失败回滚时引用行一起回滚。
- **讨论专用工具**（v2.7，`review/discuss_tools.py`，`DISCUSS_TOOLS`）：`get_plan_context`、`get_derivation(plan_id)`、`get_agent_run(run_id)` 三个只读工具，加上 v2.12 起的 `get_roadmap`（D37，读路线记录），只给「就此计划讨论」的运行（`run_discuss`，工具集合取自 `toolsets['discuss@agent-discuss-1']`：十个批处理工具加这四个共十四个；四个宿主工具来自运行的 `ToolContext.bindings`，讨论宿主绑定前三个，启动讨论的 API 路由绑定 `get_roadmap`，`review` 不 import `optimize`，缺任何一个绑定都在调用模型之前失败，该提问得到 `error` 回答），批处理的各阶段绑定的工具集合恒为那十个（`graph._wrapped_tools(..., tools=)`）。它们只接受 `DiscussContext` 里记下的本单元计划与运行，越界返回错误信封「只能查本次讨论的计划与运行」；卡上下文只含冻结事实、是否已审定、审定记录和已采纳的知识，不含任何草稿，`get_agent_run` 也不读复盘卡的起草运行与讨论运行；`get_agent_run` 的工具结果每条截断到 2000 字。全部在调用方给的只读事务里执行，不写任何表；推导与运行投影由 API 显式注入 `views.derivation.plan_derivation_in`、`views.reasoning.view_in`。
- **复盘卡起草与讨论回答**（v2.7，`graph.run_retro` / `graph.run_discuss`，单阶段，版本 `agent-retro-1` / `agent-discuss-1`，运行 `mode` 为 `retro` / `discuss`）：输出由 `growth_check.check_draft` / `check_answer` 校验，引用允许 `fact:<键>`（复盘）或 `fact:context`（讨论）、本次 `call:N`、本次返回过的 `concept:` / `know:`、上下文里的 `user:<id>`；新的作废原因 `retro_malformed`、`lesson_invalid`、`answer_malformed`（另有 `migration_027_missing`、`discussion_busy`、`draft_exists` 三个前置检查的原因），文案见[作废与跳过原因](../knowledge/concepts/agent.reject_reasons.md)；讨论回答里不合格的经验草稿只丢弃草稿、回答照常（`draft_dropped`）。
- **提议（`propose`，`agent-2`）**：独立给出动作、入场区间、止损、T1，以及下面几项结构化字段。每个价位和论点都要引用一次已写库的工具调用（`call:N`）；引用不存在，或价位找不到价格类工具支撑，整条作废（`rejected / invalid_citation`），只显示原因、不显示内容。还要通过基本校验，比如买入的止损必须低于入场。任一字段校验不过，整版作废：
  - **论点** `thesis`：1–3 条，每条带引用，宿主按顺序编号 `T1…T3`；条数不对 → `thesis_count`。
  - **失效条件** `invalidation`：1–5 条，取自条件集合（见下），每条带引用；**下次复查条件** `review_when`：0–3 条，留空等于「下一交易日」。`kind` 未登记、不能用在这一栏、参数不合法或缺引用 → `invalid_condition`。
  - **相对上版变化** `changes`：首版不填（填了 → `malformed`）；修订版必填，字段集合必须等于宿主算出的差异（`changes_mismatch`），没有差异 → `no_change`。修订只由每日复查生成。
- **条件集合 `condition-1`**（`agent/conditions.py`，失效与复查共用）：`close_below` / `close_above`（`price`）、`no_entry_by`（`date`，仅失效）、`no_entry_before_earnings`（仅失效）、`earnings_within`（`sessions` 1–10）、`regime_is`（`label`）、`sector_weak`、`rs_below`（`window` 20/60、`value`）、`new_filing`（`form` 8-K/10-Q/10-K）、`date_reached`（`date`，仅复查）、`next_session`（仅复查）。价格须在生成时最近收盘的 ±50% 内，日期须晚于本版生成交易日且不超过其后 60 天。每条判定为成立 / 未成立 / 无法判定，数据缺失一律「无法判定」，不当作成立。
- **反方**：逐条对论点给出反证（`counter: [{thesis, text, refs}]`），其余风险进 `risks`；引用不存在或论点编号不存在的条目丢弃并计数，没有反证的论点列入 `uncountered`。反方不改价位和动作。
- **Agent 计划与版本链**：结果为 `ok` 的首版生成 `agent` 计划（`plan_version = agent-2`），同一个事务里依次写计划行、运行行与工具调用、版本行 `agent_plan_versions`（`version_no = 0`、`origin = signal`、`cutoff_at` 等于运行的截止时间）和简报，任何一步失败都整体回滚，单元记 `error`。有效期 T+1…T+3、移动止损与 `plan-1` 相同，仓位按入场区间中点计算，`inputs` 另记 `as_of_session`、`version_no`、`supersedes_plan_id`、`chain_entry_cap`（入场期链上限：信号日后第 10 个交易日）。手动「重新运行 Agent」同样只生成首版；已有 `ok` / `pass` 时返回 409。首版计划进入影子跟踪和推导核对（`('agent-2', 'agent')`：价位核对 `agent_runs.output`，有效期按 `as_of_session` 与链上限推导，缺版本行 → `incomplete`）。采纳时可以把执行依据选为「Agent 建议」。
- **`agent-1` 旧版**：v2.5 之前生成的 `agent-1` 计划原样保留、只读，照常展示与影子跟踪，标「agent-1（旧版，不参与复查）」；不迁移、不回填、不进每日复查。
- **解读（`interpret`，`agent-brief-1`）**：无冲突买入单元的解读，直接取提议的摘要和第一条反证（`agent-1` 运行取反方风险）；其余单元单独跑一次单阶段解读。模型不可用或校验失败时，退回规则版摘要。
- **隔离**：Agent 在规则计划全部写入之后才开始，失败只影响 Agent 轨道。每个单元最多尝试 3 次（超过记 `skipped / max_attempts`，可「重新运行 Agent」，见下文[预算与重新运行](#预算与重新运行v28)）；从 `agent` 节点开始 1500 秒后不再启动新单元（采集节点的重试与等待不挤占它）；ChatGPT 订阅的共享用量用尽时其余单元记 `skipped / quota_exhausted`；ChatGPT 未连接 / 连接失效、所选模型不可用时整个节点记 `skipped / not_connected` / `model_unavailable`；达到每日 token 上限时记 `skipped / token_budget`。扩展池单元也跑，但排在后面，结果不推送。
- **迁移 026 未执行**：写不了版本行，所以需要提议的单元逐个记 `skipped / migration_026_missing`（主列表单元照旧得到规则版简报），解读照常运行；`agent` 阶段记 `degraded`（`migration_026_missing`），`summary.errors` 加一条 `timeline: migration_026_missing`；规则计划照常。「今日」照旧按最新运行显示，不带链。

## 账户决策

v2.11 的「Agent 自管」模拟账户使用独立的 `portfolio` 模式（`agent-portfolio-1`）。它读取该账户的七段事实：账户资金与容量、持仓与冻结计划、待成交订单、可买计划及股数上限、昨日未通过校验的订单、最近二十个交易日的已平仓、市场环境。事实来自本次截止时间已知的数据；结构与文本均脱敏，账户名称也只从清理后的事实进入提示词。

输出为 `{orders, holds, summary}`：订单只允许买入可买清单中的计划、卖出本账户已有持仓（全部或一半）、撤销本账户待成交买单；`holds` 记录继续持有的理由。格式或 holds 无效时整份拒绝，不产生订单；单笔订单违反约束时保存该笔拒绝原因，其余合格订单仍可接受。宿主先处理撤单，再逐笔检查现金、仓位、同标的唯一持仓及股数上限，不能预支卖出款。未知计划标识保留在决策原输出里，拒绝订单不伪造计划引用。

这一模式只绑定 `get_price_bars`、`get_indicators`、`get_market_context`、`get_concept`、`get_knowledge` 五个只读工具，标的限本账户持仓与可买清单的并集，不调用行情采集或下单工具；工具证据的 `phase` 为 `portfolio`。每个账户每个交易日只运行一次，同日重跑复用已存决策。运行行的 `ticker`、`signal_date`、`pool` 为空，token 计入现有每日上限，但不计入单元 Agent 覆盖率或计划三轨样本。

账户节点有独立的 600 秒预算，不沿用计划 Agent 与复查的 1500 秒起点。模型不可用、共享额度用尽、每日 token 达限或时间用完会保存跳过原因；结算在此之前完成，照计划执行账户也不依赖模型。完整账户机制、接口与限制见 [账户与模拟交易](accounts.md)。

## 计划时间线

一个单元（标的、信号日）的全部 `agent-2` 版本组成一条**版本链**，每天复查一次，你贴入的研报、新闻和判断作为证据参与复查。设计见 [v2.5 计划时间线](../specs/2026-10-28-v2.5-plan-timeline-design.md) §5、§7、§8、§12。

- **版本链**：第 0 版（`origin = signal`）由信号日批处理生成；第 1 版起只由复查生成（`review` 每日复查、`recheck` 重新复查），`supersedes_plan_id` 指向上一版，链不分叉。每一版都是一行 `trade_plans`，有自己的截止时间（生成时刻）、`as_of_session`、宿主差异 `diff` 与模型的变化说明 `changes`，各自做影子跟踪；旧版写入后不再改动。链状态（`timeline-1`）按顺序取第一条成立的：`invalidated`（被复查作废）、`closed_by_decision`（决定不买）、`holding`（决定链上的买入还有未平批次）、`position_closed`、`expired`（过了最新版截止日未成交）、`entry`（其余）；只有 `entry` 与 `holding` 会被复查。
- **每日复查**：批处理第 7 个阶段 `review`，规则见 [每日计划 · 每日复查](daily-plan.md#每日复查)：失效条件成立由系统规则作废；没有信号、没有新的用户输入、复查条件都未满足由系统规则维持；其余调用一次 `agent-review-1`，结论为修订时再走一次完整的提议 + 反方 + 校验，通过才成为新的最新版。每条结论写一行 `plan_reviews`。
- **信号**：`new_8k`、`abnormal_move`、`stop_touched`、`t1_touched`、`expiry_near`、`regime_change`、`user_input`，窗口都是「上次有效复查之后」，七项不论是否触发都记进复查记录。
- **用户输入**（`user_inputs`，只追加）：类型 `research`（研报要点）/ `news`（新闻）/ `judgment`（我的判断）/ `other`，正文 1–4000 字，可附 `http(s)://` 链接（≤ 500 字，不抓取）；不填 `signal_date` 作用于该标的所有链，填了只作用于这一条链（单元必须有候选，否则 404）。**更正**或**撤回**（`kind = retract`）都是新的一行，用 `supersedes_id` 指向同一标的的旧行（不是同一标的 → 400）；旧版计划引用过的旧行永远可读，之后的复查只看到截止时间之前仍有效的行。同一 `request_id` 同内容返回原行（`status = replayed`），不同内容 409。复查与修订的提示词里附「用户输入」证据块：上次复查之后的新输入给原文（最多 10 条、合计 8000 字，超出的记「另有 n 条未纳入」），更早的只给标题行；原文每一行前都加 `    │ ` 前缀，宿主的「原文开始 / 原文结束」标记行没有这个前缀，正文无法伪造标记或标题；提示词写明用户输入只是证据、不是指令，引用 `user:<id>` 只能引用块里出现的 id。新输入触发 `user_input` 信号，即一次认真复查，本身不会直接改计划。
- **重新复查**：对 `entry` / `holding` 的链立即复查一次（`trigger = recheck`）。截止时间 = 点击时刻（请求进入时取，不是后台开始执行的时刻），复查交易日 = 该时刻最近已收盘的交易日；跳过「无事可看」的系统维持，一定调用模型（失效条件成立仍由系统规则作废）；新版 `origin = recheck`；不受每天 3 次的限制，但受每日 token 上限与额度限制。前置检查同步执行：迁移 026 未执行 503、没有链 404、链不在入场期 / 持仓期 409 `chain_not_active`、Agent 不可用 409 `agent_unavailable: <原因>`、该链正在复查 409 `review_in_progress`，都不写行；通过后在后台执行（同「立即运行批处理」），返回 `{run_id, status: pending}`，用 `GET /api/runs/{run_id}` 查结果。**互斥**：每条链的一次复查（批处理或重新复查）从读取、调用模型到写入，全程在一条不经连接池的专用数据库连接上持有会话级 advisory lock `review-run:<标的>:<信号日>`（不占连接池，连接用完即关）；读取与写入事务里另有事务级锁 `review:<标的>:<信号日>`。前置检查只试运行锁，不试探事务级写锁（试探若恰好落在批处理的写事务里，会让批处理在花完 token 后丢掉这次复查）。所以批处理正在复查这条链（包括正在等模型）时点「重新复查」→ 409 `review_in_progress`；连点两次，第二次 409；重新复查正在跑时批处理跳过该链（`review_in_progress`，不写行）。已排队的后台任务开始时先取这把锁，拿不到就以 `review_in_progress` 结束，不调用模型。模型调用期间链被别的写入改动（例如你提交了不买的决定）时，这次复查不写复查记录，但模型运行照样记成 `skipped`（`review_in_progress`）并带上 token，计入每日 token 上限。
- **入口**：
  - API：`GET /api/workbench/chains?ticker=`（该标的全部链，含已结束的，信号日倒序：`signal_date`、`state`、`mode`、`version_no`、`head_plan_id`、`latest_review`（`outcome`、`decided_by`、`created_at`、`reason`，没有有效复查为 `null`），供「标的」页列出 Agent 计划）；`GET /api/workbench/chains/{ticker}/{signal_date}`（链视图：`state`（`state`、`mode`、`basis`）、`versions`（每版的计划行、生成运行的输出与反方、`changes`、`diff`、`plan-shadow-1` 影子结果）、`reviews`（全部复查记录，带被复查与产生的版本号）、`inputs`（作用于这条链的用户输入，带 `ref`、`status`：`active` / `corrected` / `retracted`））；`GET /api/workbench/plan-reviews?session=`（某个复查交易日的全部复查记录）；`POST /api/workbench/chains/{ticker}/{signal_date}/recheck`；`GET /api/workbench/user-inputs?ticker=&signal_date=`（不带 `signal_date` 时列出该标的全部输入）与 `POST /api/workbench/user-inputs`（body `{request_id, ticker, signal_date?, kind, body, source_url?, supersedes_id?}`）。读取接口都在只读事务里执行，不调用外部数据源；迁移 026 未执行时这些接口都返回 503「迁移 026 未执行」。
  - 命令行 `python -m investment_assistant.workflow.nodes.review`：`chain --ticker X --date YYYY-MM-DD`（打印链状态、各版本、复查记录与用户输入，只读；没有链退出码 1）；`recheck --ticker X --date YYYY-MM-DD`（同步重新复查，打印复查记录与新版 id；冲突退出码 1）；`check --session YYYY-MM-DD`（只读）：该交易日每条应复查的链（按批处理开始时的最新版判定处于 `entry` / `holding`，未被 `object_cap` 排除）恰有一条有效批处理复查，或最新版就是当天生成的（`fresh_version`）；列出缺复查的链及其 `skipped` / `error` 行与阶段跳过原因；「批处理开始时」取当天 `review` 阶段证据行的开始时间（没有阶段证据时取该链当天第一条批处理复查记录；两者都没有时，当天重新复查产生的版本不算作批处理看到的版本），被排除的链与无行跳过的原因取自该行的 `detail.excluded`（逐链，不截断）。当天生成的每一版 `supersedes_plan_id` 指向上一版、`cutoff_at` 等于生成运行的 `cutoff_at`、输出与反方里的引用都成立（`call:N` 是生成运行自己的工具调用，`user:<id>` 是该标的的输入且 `created_at ≤ cutoff_at`），当天复查记录的引用同样核对。任一不满足退出码 1；参数错误（含不是美股代码的 `--ticker`）、数据库不可用或迁移 026 未执行退出码 2；其余异常照常抛出。
- **怎么验证一次**：批处理跑完后执行 `plan_review check --session <当天>`，应为「通过」；信号日当天新生成的链显示「当天生成（fresh_version）」。任选一条入场期的链，`POST /api/workbench/user-inputs` 贴一条判断（带 `signal_date`），再 `POST …/recheck`，轮询 `/api/runs/{run_id}` 到 `done`；`GET /api/workbench/chains/…` 的 `reviews` 末尾多一条 `trigger = recheck` 的记录，其 `cutoff_at` 等于点击时刻、`user_input_ids` 含刚贴的输入、`signals` 里 `user_input` 已触发。
- **已知限制**：
  - `check` 按当前记录的决定与成交判定链状态（与复查阶段同一口径）；版本与复查记录按批处理开始前的为准。
  - 重新复查在后台线程里执行，服务重启会丢掉还没跑完的任务（不写行，锁随连接关闭释放）；页面轮询到 `error` 时可以再点一次。每条正在复查的链多占用一个数据库连接（持锁用）。
  - 用户输入的时间以数据库写入时刻为准，不能补登更早的时间；更正、撤回只对之后的复查生效，不回填旧版。
  - `GET /api/workbench/user-inputs` 不带 `signal_date` 时，按该标的的候选日逐个读取各链的输入，候选日很多时较慢。

### v2.9：可执行的计划（`agent-3`、活跃链、再次触发、退出期、持仓状态）

设计见 [v2.9 可执行的计划](../specs/2026-10-30-v2.9-executable-plans-design.md) §4、§5.5、§7 与 §21 的修订。上线后新开的链首版是 `agent-3`；旧的 `agent-2` 链照常复查，它们的修订版按 `agent-3` 生成，链里出现 `agent-2` 与 `agent-3` 混排（按版本行的 `schema_version` 区分），`agent-2` 版本没有执行字段，计划表按 `zone_limit` + 10 日显示、影子只算 `plan-shadow-1`。

- **执行字段**：`agent-3` 输出多两个必填项：`entry_type`（`zone_limit` / `breakout_stop` / `next_open`，模型只选类型，开盘三种情形的做法由宿主按类型生成，见[入场类型](../knowledge/concepts/plan.entry_type.md)与[开盘三种情形](../knowledge/concepts/plan.open_scenarios.md)）与 `max_hold_sessions`（整数 5–20，默认 10，见[最长持有](../knowledge/concepts/plan.max_hold.md)）。`CHANGE_FIELDS` 加这两项；`valid_until`、`entry_cap` 是宿主字段，只出现在版本行的宿主差异 `diff`（`host_only: true`），不进模型的 `changes`。提示词（`PROPOSE_PROMPT_V3`）说明三种类型、最长持有与「再次触发」的续期写法；条件表是 `condition-2`（`held_sessions_reached` 只能作复查条件，失效条件由宿主按 `max_hold_sessions` 注入）。校验新增原因：`invalid_entry_type`、`invalid_max_hold`、`stop_lowered`（持仓期不能下移止损）、`stop_above_close`（持仓期止损不能高于最新收盘）、`active_chain`（见 `REASON_TEXT`，前端同步）。
- **首版的「同标的上一条链」背景块**：见[每日计划 · v2.9](daily-plan.md#v29一只股票一条活跃链再次触发与退出期)；块的哈希存在运行的 `output.input_digest.previous_chain`。`get_position` 工具现在只读截止时间之前录入并成交的成交（`list_trades(cutoff=截止时间)`，D33），与首版、复查、修订共用的截止时间一致。
- **一只股票一条活跃链**：`plan` 节点（实现在 `signals` 节点的规则循环里）为已有活跃链的标的写 `chain_retriggers`，`agent` 节点不为这些单元提议；见[一只股票一条活跃链](../knowledge/concepts/system.active_chain.md)与[再次触发](../knowledge/concepts/agent.retrigger.md)。第八项信号 `retrigger` 的窗口是 `chain_retriggers.created_at ∈ (上次有效复查的截止时间, 本次截止时间]`（R4）。
- **退出期与持仓状态**：持仓期的作废进入退出期，退出期的链不再复查；每个持仓标的每个交易日有一行 `holding_status`，见[持仓状态](../knowledge/concepts/review.holding_status.md)。链状态的九种见[版本链状态](../knowledge/concepts/review.chain_state.md)。
- **链视图**：`plan_review chain`（与 `GET /api/workbench/chains/{ticker}/{signal_date}` 的 `chain_view`）另给 `retriggers`（并入的单元，每条带它产出的版本号 `version_no`，没有产出为 `null`）、`holding_status`（该链的全部状态行）与 `notes`（备注）；`state` 是 `timeline-2` 的 `{state, mode, active, basis, basis_ref, timeline_version, renewable}`。
- **已知限制（v2.9 补充）**：并入的链当天被宿主作废时，这次触发只剩规则基线，不补开新链（要等下一次触发）；无链持仓与同标的新入场链并存时，持仓状态按标的一行（全部未平批次）、链上的买入按链，两边的股数可能不同；分次买入使加权成本上移后，`reached_1r` 可能由真变假，时间止损重新生效；旧 `agent-2` 链的持仓止损按链最新版取（持仓期修订上移的止损开始生效）。

## 就此计划讨论

从计划卡、「标的」页的计划行、复盘卡进入，问「这个止损怎么来的」「为什么这版放宽了入场区间」：Agent 只读这份计划（或这张卡）的推导链、运行记录、概念卡和你的经验，给带引用的解释，**不改任何东西**；需要时可以起草一条经验（落为草稿，等你审定）。设计：[v2.7 §8](../specs/2026-10-28-v2.7-growth-loop-design.md#8-就此计划讨论)，版本 `agent-discuss-1`，模块 `review/discussion.py`（用例）、`review/discuss_tools.py` 与 `review/agent_runs.py`（工具与校验）。

- **新建与提问**：`POST /api/workbench/discussions`（body `{request_id, context: {kind: 'plan', plan_id} | {kind: 'card', card_id}}`）新建一次讨论（同一上下文可以有多次），再 `POST /api/workbench/discussions/{id}/turns`（body `{request_id, body, attachment?: {kind: 'draft' | 'knowledge', id, version?}}`，`body` 1–4000 字）提问。提问的前置检查同步执行并按顺序：参数 400，迁移 027 未执行 503，讨论不存在 404，同一 `request_id` 同内容返回原行（`status: replayed`）、不同内容 409 `request_id_conflict`，Agent 不可用 409 `agent_unavailable: <原因>`，当日 token 达上限 409 `token_budget`，上一个问题还在回答 409 `discussion_busy`；通过后写下你的这一轮，在后台调用模型，返回 **202** `{turn_id, run_id, status: pending}`。
- **读取与等待**：`GET /api/workbench/discussions/{id}` 返回上下文与按顺序排列的全部轮次，回答紧跟在它的提问后面；没有回答的提问带 `state`：`pending`（后台还在跑）或 `interrupted`（服务重启过或后台出错，页面写「没有收到回答（服务可能重启过），可以再问一次」，不算忙）。页面每 2 秒读一次，最长 600 秒。`GET /api/workbench/discussions?plan_id=|card_id=` 列出某个上下文的讨论。
- **截止时间 = 提问时刻**：回答的 `cutoff_at` 就是你那一轮的 `created_at`，工具读到的是截至这一刻的数据；提示词同时给出「计划当时的截止时间」，要求区分「当时看到的」与「之后发生的」（这靠提示词，机制上保证不了模型的措辞）。
- **工具**：十个批处理工具加三个讨论专用工具（见上），模型**没有任何写工具**；工具全部在 `service.read_only_tx()` 的真实只读事务里执行（宿主传入 `DiscussContext.read`，不是 `service._read_only`）。讨论只写四类行：你的一轮与回答（`discussion_turns`）、这次回答的运行与工具调用（`agent_runs`，`mode = discuss`；`agent_tool_calls`，`phase = discuss`）、可选的一条经验草稿（`knowledge_drafts`），测试比对一轮前后全库各表行数；每次 Agent 运行还会让 LangGraph 的检查点表（PostgresSaver）多几行，这不算在四类里，比对时把检查点表排除在外。讨论的 token 计入每日上限。
- **提示词与校验**：输入是上下文摘要（`[fact:context]`，含你输入过的 `user:<id>`）、最近 10 轮合计不超过 12000 字的问与答、你这一轮；附件原文另起一块，标「用户提供，非证据，不可引用」。回答校验（`check_answer`）：形状与长度 `answer_malformed`；`refs` 非空且每项是本次 `call:N`、本次返回过的 `concept:` / `know:`、上下文里的 `user:<id>` 或 `fact:context`，否则 `invalid_citation`，这一轮记 `rejected`，页面写「这次回答没有通过引用校验：<原因>」，原文折叠在「仍要看原文（未通过校验）」后面。经验草稿另校验（适用范围、反例必填，`target` 必须是附件里那条知识或本次返回过的），不合格只丢弃草稿、回答照常，轮次 `detail.draft_dropped` 记原因。
- **「存为补充信息」与「就这条改写」**：前者把你这一轮的原文按 `POST /api/workbench/user-inputs` 存为用户输入，`request_id = uuid5(NAMESPACE_URL, 'discussion-turn:' + <这一轮的 request_id>)`（前后端同一算法，`discussion.user_input_request_id`），重复点击幂等，讨论视图按它查出这一轮对应的 `user:<id>`，之后的问题里它可被引用；讨论本身不触发复查，要进计划得点「重新复查」。后者把一条草稿或知识的原文作为你这一轮的附件交给模型，改写结果仍是草稿，要在「我的知识 → 草稿」审定。
- **出错**：后台任何异常都会写一条 `status = error` 的回答（原因脱敏），不留无回答的悬空轮；额度用尽记 `error / quota_exhausted`。
- **怎么验证一次**：从计划卡问「这个止损怎么来的」，回答带引用，「看这次回答读了什么」打开推理视图可读；抽查全库，除上面四类行（和 LangGraph 检查点表的运行记录）外没有变化；让它起草一条经验，草稿出现在「我的知识 → 草稿」，采纳后成为新条目。
- **已知限制**：不做流式输出，一次回答可能要一两分钟；服务重启会中断正在进行的回答；`get_trigger`、`get_position` 不按截止时间过滤（沿用现状）；`get_agent_run` 不读复盘卡的起草运行与讨论运行。

## 推理阅读视图

`GET /api/workbench/agent-runs/{run_id}/reasoning`（v2.6，`views/reasoning.py`，只读事务、不调用外部数据源）把**一次 Agent 运行**按阅读顺序摊开：`run_id` 不是 UUID → 400，不存在 → 404，数据库不可用 → 503。覆盖 `propose`（首版）、`revise`（修订）、`review`（单阶段复查）、`interpret`（解读）、`retro`（复盘卡起草）、`discuss`（讨论回答）六种 `mode`，计划卡、版本链每版与复查记录各自链到生成它的运行。返回 `{run, legacy, version, review, steps, notes}`：

- **步骤**（每步 `{kind, title, concept}`，`concept` 是这一步的概念卡）：首版 `read`（提议者读了什么）→ `proposal`（动作、摘要、价位、论点 `T1…`、失效与复查条件、修订另含 `changes` 与宿主 `diff`）→ `check`（校验结果，**在反方之前**，与图里的执行顺序一致）→ `read`（反方读了什么）→ `counter` → `response` → `outcome`（生成的计划行状态与推导一致性，只对生成了计划的运行）；修订在最前面多一步 `evidence`（触发这次修订的复查：复查交易日、七项信号、条件判定、给模型的用户输入 id，并链到该复查自己的单阶段运行）；单阶段复查是 `evidence → read → verdict → check`；解读是 `read → interpretation → check`；复盘卡起草是 `evidence`（冻结事实的十个键与结束类型）`→ read → draft`（`summary` 标题、`items` 叙述各句与决定回顾、`lessons` 教训草稿）`→ check`；讨论回答是 `question`（你的问题、标「用户提供，非证据，不可引用」的附件、讨论对象）`→ read → answer`（回答、引用、经验草稿；被丢弃的草稿写在 `note`）`→ check`。
- **`read`**：每个调用 `{seq, ref: 'call:N', tool, tool_text, args, result, error, result_sha256_8, called_at, concept, fields, cutoff_exempt, cutoff_note}`。`fields` 把结果里的名字对应到概念卡：顶层键（`atr14`）、宏观序列（`macro.BAMLH0A0HYM2`）、`get_trigger` 返回的规则 id 取值（`trend_reclaim` → `rule.trend_reclaim`）、`get_concept` 返回的 `id`。`get_concept` 调用 `cutoff_exempt: true`；`get_knowledge` 调用的 `cutoff_note` 写「我的知识按截止时间读取：只含截止时间之前写入的版本」（`cutoff_exempt` 仍为 false），返回的 `entries[].ref` / `title` 在页面上链到条目页。
- **`check`**：列出这类运行适用的检查项，结果 `pass` / `implicated` / `not_recorded` / `not_run`。`ok` / `pass` → 全部通过；`rejected` → 作废原因出现在该项原因集合里的**全部**项标 `implicated`（校验器遇到第一处失败就停，只记原因，不推断哪些项通过了，`note` 写明这一点），其余 `not_recorded`；`error` / `skipped` → 全部 `not_run`，附原因文案。
- **`response`（回应）**：`agent-2` 流程没有单独的「回应」步骤，也不新增；这里的「回应」= 本版之后以本版为被复查对象的复查记录（结论、判定方、理由、触发的信号，修订时附新版的版本号与「相对上版变化」）。没有复查就写「回应：本版之后还没有复查」。`agent-1` 运行与没有生成计划的运行没有这一步。
- **反方读了什么**：第二个 `read` 步骤固定出现；反方跑了但没调工具写「反方没有调用工具」，反方没有运行（提议已作废或运行出错 / 跳过）写「反方没有运行」；提议者那一步没有记录到调用写「没有记录到工具调用」。
- **推导一致性读不出**：`outcome` 步骤的 `consistency` 为 `null`，`consistency_error` 给一行原因，`notes` 同时加一条「推导一致性没能读出（consistency_error）：…」，其余步骤不受影响。
- **作废的运行**（`rejected` / `error` / `skipped`）：已记录的工具调用照样给出；输出或反方存了就照常显示（例如入场期修订版因计划行作废而 `rejected / plan_invalid`，提议和反方都在），没存的那一步写「这次运行作废：<原因文案>，只保留工具调用」，`check` 照给。
- **`agent-1` 旧运行**：`legacy: true`，`notes` 写「agent-1（旧版）：没有失效条件、下次复查条件与逐条对应的反证，也不参与复查；以下按原样显示」；步骤是 `read → proposal`（论点作为「理由」）`→ check → read → counter`（只有 `risks`）`→ outcome`，没有 `response`。
- **迁移 026 未执行**：没有 `version`、`evidence`、`response`，`notes` 写「迁移 026 未执行，没有版本与复查记录」。

## 模型层（v2.8）

Agent 的模型调用走你自己的 ChatGPT 订阅（Sign in with ChatGPT）：不再有 API 密钥，也没有智谱。设计见 [v2.8 每日工作流 §8](../specs/2026-10-29-v2.8-daily-workflow-design.md)，协议版本 `siwc-1`（`CHATGPT_PROTOCOL_VERSION`，写进 `agent_connection_events.detail.protocol`）。

- **连接**（`core/llm/connection.py` 是读写 `agent_connections` / `agent_connection_events` 的唯一模块；协议在 `core/llm/chatgpt.py`）：在「设置 → Agent 连接」点「连接 ChatGPT」，服务在 `127.0.0.1` 上起一个只收一次回调的监听（端口默认由系统分配，`WORKBENCH_CHATGPT_CALLBACK_PORT` 可固定；不用服务的 8787 端口，因为它挂着认证），返回授权地址在本机浏览器里打开。授权参数：动态注册（首次 `client_id = dynamic_agent_client` + `agent_name_hint`，之后用保存的 `client_id`）、PKCE `S256`、`state` / `nonce` / `code_verifier` 各 32 字节随机、`resource`、`ext_agent_host_id`（本机固定的 `urn:uuid:…`，之后永不改变）、已知邮箱加 `login_hint`。回调只接受 `GET /auth/callback`，`Host` 必须是 `127.0.0.1:<端口>`，`state` 必须恰好出现一次且常数时间相等，不符的请求回 400 / 404 但不结束这次登录；回调里的 `client_id` 必须是 `^[A-Za-z0-9_-]{1,200}$`、不是 `dynamic_agent_client`、与已保存的一致，否则 `registration_incomplete`；换令牌之前先把 `client_id` 写库（一次性 code 可能换失败，注册结果不能丢）。待定的登录只存在服务进程内存里，10 分钟（`LOGIN_TTL_S`）没有回调、点「取消」或服务重启都使登录作废，状态回到原值（`disconnected` / `needs_login`）。
- **令牌与校验**：令牌端点响应必须是 `bearer`、`expires_in > 0`、有 `access_token` 与 `refresh_token`、`scope` 是字符串；ID token 校验 `iss`、`aud`（及 `azp`）、`exp`、`nonce`（登录时）与 `sub` 不变（再次登录 / 刷新时，不同账号 → `account_mismatch`），**不验签名**（令牌直接从令牌端点经 TLS 取得，D28）。`scope` 不含 `chatgpt.tokens.use.direct`（计划用量共享）→ 状态仍是 `connected`，但 Agent 记 `not_configured`，设置页提示「已登录，但这个账号没有开启计划用量共享」。
- **凭据存储**：明文存在本机数据库的单行表 `agent_connections`（与 `.env` 里的密钥同一安全级别，表进数据库备份时要当作机密）。令牌不进日志、不经任何接口回显（`status_view` 只返回状态、脱敏邮箱、到期时间、模型、`last_error` 等）、不进 `agent_connection_events`（只放事件类型、安全错误码、到期时间和模型 slug）、不进异常文本（协议错误一律重建为 `ChatGPTError(code, message)`，`code` 是 `^[A-Za-z0-9_.:-]{1,100}$` 的安全标识符，`message` 是固定中文，响应体只用来读错误码）。
- **刷新**：每个 Agent 单元 / 每次讨论回答构造模型前取令牌（`access_token()`）：剩余 ≥ 300 秒直接用；不足 300 秒且已过 `earliest_refresh_at` → 在 `SELECT … FROM agent_connections WHERE id = 1 FOR UPDATE` 的事务里向令牌端点换新令牌，**先写库、提交，再把新令牌交给调用方**（进程内另有一把线程锁；两个线程同时来只刷新一次，第二个拿到新令牌）。过期但没到 `earliest_refresh_at` → `refresh_not_ready`（本单元 `model_error`，下个单元再试）。令牌端点的授权码 / 刷新请求是 POST，不会在可能已发出后重发（`fetch-1` 的 `retry_post=False`：只在请求没发出或 429 时重试）；发现文档、模型清单是 GET，按 `fetch-1` 重试。refresh token 随每次刷新滚动 30 天，所以每天运行一次连接就不会过期。
- **刷新失败**：`invalid_grant`、`invalid_refresh_token`、`token_expired`、`refresh_token_expired`、`refresh_token_invalidated`、`refresh_token_reused`、`invalid_id_token`、`account_mismatch` → 清空令牌、状态 `needs_login`、`last_error`、一条 `refresh_failed` 事件；网络错误、5xx 等 → 不改状态，只写 `last_error`，本单元 `model_error`。同一次失效只通知一次：`first_failure_since_ok(since=…)` 在连接当前是 `needs_login`、最近一条需重新登录的 `refresh_failed` 不早于 `since`、且其后没有 `connected` / `refreshed` 事件时为真；`daily` 在执行收尾时传「最近一次真正发出这条通知的执行做检查的时刻」（`summary.connection_notice.checked_at`，从没发过则不传），于是每次失效在它之后第一次命中的执行里通知一次——中间夹着休市日、在 `agent` 之前就失败的运行、没有重新执行 `agent` 的续跑，都不会把检查的起点推过一次还没通知的失效；失效发生在某次执行中途时，那次执行通知，下一次不再重复；重新连接后再失效是新的一次。触发条件见[预算与重新运行](#预算与重新运行v28)里的「连接失效通知」。
- **模型调用证明连接不可用**：`SiwcChatOpenAI`（由已存连接取令牌时）在流出错时把结果写回连接：`subscription_sharing_user_not_eligible`、`subscription_sharing_invalid_user`、`chatpass_v2_scope_not_authorized`、`chatpass_v2_invalid_authorization_context` → `mark_needs_login`（状态 `needs_login` + 一条 `refresh_failed` 事件）；HTTP 401 / `invalid_token` / `invalid_api_key` → `report_unauthorized`：令牌是 10 分钟内刚登录或刚刷新的，说明刷新后仍被拒，同样改成 `needs_login`；否则把该令牌当作已过期，下一个单元强制刷新。写回失败只记日志，不掩盖原来的模型错误；`classify_error` 仍是纯函数。
- **断开**：向 `revocation_endpoint` 撤销 refresh token（5xx 重试一次），不论撤销是否确认都清空令牌和账号信息（`subject`、邮箱）；撤销未确认时返回 `revoked: false`，页面提示「本机凭据已删除，但远端断开未确认，请到 ChatGPT 设置里断开这个应用」。`host_id`、`client_id` 与所选模型保留。换 ChatGPT 账号 = 先断开、再连接；从 `needs_login` 直接重新登录必须是同一个账号（否则 `account_mismatch`）。
- **模型清单与选择**：`GET {RESOURCE}/models` 返回的 `models[]` 里 `visibility == "list"` 的项（保持服务器顺序，显示 `display_name`、保存 `slug`），不写死任何模型名；选择必须在当前清单里，所选模型之后被拒（`model_not_found`）→ `model_unavailable`。
- **请求（路径 A）**：`model.chat_model(settings)` 取令牌后构造 `SiwcChatOpenAI`——`ChatOpenAI` 的子类，Responses 模式（`use_responses_api`）、`output_version='v0'`、`store=False`、`streaming=True`、`temperature=None`、`max_retries=0`（重试交给每个单元的尝试次数闸）、`httpx.Timeout(180, connect=10)`、`base_url = RESOURCE`，HTTP 客户端读环境里的代理变量，并去掉请求里的 `OpenAI-Organization` / `OpenAI-Project`（这是 ChatGPT 账号，不是平台账号）。子类覆写 `_get_request_payload` 调 `chatgpt.rewrite_payload`（纯函数）：① `input` 开头所有 `system` / `developer` 的纯文本项取出，按顺序用两个换行拼成 `instructions`，其余位置的 `system` 项改为 `developer`；② 函数工具按 `TOOL_SHAPE` 摆放（`top_level` 顶层 / `namespace` 包进 `{"type": "namespace", "name": "workbench", …}` / `additional_tools` 作为 `input` 开头的 `{"type": "additional_tools", "role": "developer", …}` 项）；回传给模型的 `function_call` 项去掉 `id`（`store` 为 false，没有可引用的条目），`namespace` 形状下带上所属 namespace；③ 合并 `REQUEST_OVERRIDES`；④ 最后一步删除 `FORBIDDEN_FIELDS`（官方限制页的 16 个：`background`、`conversation`、`max_output_tokens`、`max_tool_calls`、`metadata`、`moderation`、`multi_agent`、`prompt`、`prompt_cache_retention`、`safety_identifier`、`temperature`、`top_logprobs`、`top_p`、`truncation`、`user`、`previous_response_id`）。`_stream` 覆写读流：`response.failed` / `error` 事件按错误码抛 `ChatGPTError`，`response.incomplete` → `response_incomplete`，流在 `response.completed` 之前结束 → `stream_interrupted`；纯文本块合并为字符串，所以四张子图、`ToolNode` 与校验器一行不改。`usage_metadata` 来自 `response.completed`（`input_tokens`、`output_tokens`，缓存与推理 token 在 `input_token_details.cache_read` / `output_token_details.reasoning`）。
- **选路与核查期开关**：模型层按 [v2.8 spec §8.7](../specs/2026-10-29-v2.8-daily-workflow-design.md) 由 §17 的核查结果选路；§17 还没有结果时先按路径 A 实现（上面这条），`TOOL_SHAPE`（默认 `namespace`）、`FORBIDDEN_FIELDS`、`REQUEST_OVERRIDES`（默认空）、`INSTRUCTIONS_FROM_SYSTEM`（默认开）是 `chatgpt.py` 里的常量，核查期可用环境变量覆盖：`WORKBENCH_CHATGPT_TOOL_SHAPE`（`top_level` / `namespace` / `additional_tools`）、`WORKBENCH_CHATGPT_EXTRA_FORBIDDEN`（逗号分隔的额外字段）、`WORKBENCH_CHATGPT_OVERRIDES`（JSON 对象，合并进请求）、`WORKBENCH_CHATGPT_INSTRUCTIONS_FROM_SYSTEM`（`0` / `1`）。这些变量只在核查期使用，核查结果追加进 spec §17 后改成常量并删掉变量，不进「设置 → 环境变量」。
- **错误分类**（`model.classify_error(exc, secret) -> (reason, detail)`；`detail` 去掉当前令牌、本进程发出过的令牌、`Bearer …` 与 JWT 形状的片段，不含请求内容，截断 300 字）：

| 来源 | 条件 | `reason` | 影响 |
| --- | --- | --- | --- |
| Responses（HTTP 或 `response.failed`） | `subscription_sharing_usage_limit_exceeded` | `quota_exhausted` | 本次其余单元跳过，推送里如实标注 |
| 同上 | `subscription_sharing_usage_unavailable`、`subscription_sharing_user_unavailable` | `model_error` | 走尝试次数闸 |
| 同上 | `model_not_found` | `model_unavailable` | 本节点其余单元跳过；设置页提示重新选模型 |
| 同上 | `subscription_sharing_user_not_eligible`、`subscription_sharing_invalid_user`、`chatpass_v2_scope_not_authorized`、`chatpass_v2_invalid_authorization_context`、`invalid_token`、`invalid_api_key`、HTTP 401 | `not_connected` | 同上；调用方用 `agent_connection.mark_needs_login` 把状态改成 `needs_login`（401 只在刷新后仍 401 时） |
| 同上 | `subscription_sharing_unsupported_capability`、`subscription_sharing_route_not_supported`、HTTP 400 / 422 | `model_error`，`detail` 带安全的 `param` | 走尝试次数闸，三次后 `max_attempts` |
| 刷新 | 需重新登录的八种（见上） | `not_connected` | 设置页红条「ChatGPT 连接已失效，请重新连接」 |
| 网络、5xx、429（非额度）、`stream_interrupted`、`response_incomplete`、超时 | — | `model_error` | 走现有尝试次数闸 |
| 检查点表缺失 | — | `checkpoint_unavailable` | 不变 |

- **删智谱**：旧供应商的密钥、`WORKBENCH_AGENT_MODEL`、`WORKBENCH_AGENT_BASE_URL` 三个环境变量不再读取（真实 `.env` 里的几行由 v2.8 的上线步骤经你授权后删除）；`DEFAULT_MODEL`、`DEFAULT_BASE_URL`、`TEMPERATURE`、`REQUEST_TIMEOUT_S`、`CLIENT_RETRIES` 和旧额度码判断全部删除。已有 `agent_runs` / 计划 / 简报里的旧 `provider` / `model` 保留只读，页面照常显示当时的模型名。`agent_runs.provider` 现为 `chatgpt`，`model` 为所选模型的 slug（没有选模型时单元记 `skipped / model_unavailable`，`model` 列写空串，因为该列不允许为空）。

## 预算与重新运行（v2.8）

- **预算窗口**：`agent` 节点开始时把墙钟时间记进检查点状态（`agent_started_at`），节点内换算成单调时钟；`agent` 与 `review` 共用这个 1500 秒窗口，到点后不再启动新单元（`skipped / stage_budget`）。采集节点用掉的时间、节点级重试和 60 秒等待都在窗口之外；`retro` 起草复盘卡另有自己的 1500 秒。续跑时窗口从续跑那一次执行开始重新计。
- **尝试次数闸与重置**：每个单元、每种模式（`propose` / `interpret`）最多尝试 3 次，第 4 次记 `skipped / max_attempts`；只有**最近一次重置之后**的运行才算进这 3 次，`agent_runs.attempt` 仍是全部历史运行数加 1，不清零。重置是只追加表 `agent_attempt_resets` 的一行（迁移 028，`request_id` 幂等）。
- **「重新运行 Agent」**（运行详情的 Agent 结果行、计划页）：`POST /api/workbench/agent-runs/rerun`，body `{ticker, signal_date, mode, request_id, origin}`（`mode` ∈ `propose` / `interpret`；`origin` ∈ `run_detail` / `plan_page` / `api`，缺省 `api`），返回 `{reset_id, run_id, replayed}`。条件：该单元这个模式最近一次运行是 `skipped / max_attempts` 或 `error`，否则 `409 not_needed`；单元不存在 404；另一次每日工作流正在运行时 `propose` 返回 409 `daily_running`（那次执行可能正在处理同一个单元）；参数不对（模式、`origin`、非 UUID 的 `request_id`、`propose` 单元不是无冲突的买入）400；迁移 028 未执行 503。`propose` 同步立即重跑一次（和手动单元运行一样，请求会等到这一次运行结束，`run_id` 是新的 Agent 运行），`interpret` 只重置，由同一交易日的续跑或手动运行执行（下一次定时运行已是新的交易日）；同一个 `request_id` 重复提交只返回已记下的那次重置（`replayed: true`），不再重跑。`review` 的尝试闸照旧按天，不在本版范围。
- **续跑 `agent` 节点**：已完成的单元由 `already_completed` 跳过，失败的单元按尝试次数再试。
- **连接失效通知**：某次 `daily` 执行的 `agent` 或 `review` 节点因 `agent_unavailable: not_connected` 整个跳过（或有单元中途遇到 `not_connected`），且 `first_failure_since_ok` 为真时，执行收尾发一条「⚠️ ChatGPT 连接已失效」（影响：今天的 Agent 建议与复查已跳过；处理：到「设置 → Agent 连接」重新连接），每次执行至多一条。检查的起点是最近一次**发出**通知的执行的 `checked_at`，没发出过则不限；结果记在该次 `job_reports.summary.connection_notice`（`{sent, reason, checked_at}`；这次失效已通知过是 `sent: false, reason: already_reported`，连接已经恢复是 `not_failed`，通知发不出去时下一次执行会再试）。没有重新执行 `agent` / `review` 的续跑不通知。
- **中途发现连接不可用**：一个单元遇到 `not_connected` 或 `model_unavailable`（`classify_error`）时，它自己记 `error`，本节点其余单元（`agent` 的 propose / interpret，以及随后 `review` 的各条链）记 `skipped`、原因相同，不再请求模型——与额度用尽 `quota_exhausted` 同一机制；`summary.agent.connection_lost` 记原因，`review` 阶段读它。`model_unavailable` 的「通知一次」没有实现（规格 §13.2 没有对应模板），是已知限制。
- **Agent 连接接口**（`GET /api/settings/agent-connection` 等六个）：状态只含 `status`、`email_masked`、`scopes_ok`、`access_expires_at`、`refresh_expires_at`、`last_refresh_at`、`last_error`、`model`、`model_display_name`、`provider_setting`、`pending`；登录已有未完成的一次时 409 `login_pending`（先「取消」），发现文档无法验证 502 `discovery_failed`，没有连接可断开 / 没有连接取模型 409 `not_connected`，所选模型不在清单 400；错误体只带安全错误码。

## 配置

| 环境变量 | 说明 |
| --- | --- |
| `WORKBENCH_AGENT_PROVIDER` | `chatgpt`（默认）或 `off`；其它值 → 配置错误，Agent 记 `not_configured` |
| `WORKBENCH_CHATGPT_CALLBACK_PORT` | 可选，固定登录回调监听的本机端口；不设（0）由系统分配 |
| `WORKBENCH_AGENT_DAILY_TOKENS` | 可选，每日 token 上限（`input + output`） |

模型不再由环境变量选择：在「设置 → Agent 连接」登录并选模型，选择存在 `agent_connections.model`。

首次使用前建检查点表：`python -m investment_assistant.analysis.agent setup`。

## 入口与验证

- 页面：「今日」的 Agent 建议卡片（引用编号可以展开，看到工具调用的参数、返回值和哈希前 8 位；失败时有「重新运行 Agent」）；「复盘 → 轨道对比」里点 Agent 运行 id。
- 「今日」接口（`GET /api/workbench/today`）：`agent-2` 单元的 `agent` 取**链的最新版**的运行与计划（不是最新一次运行），并带 `chain`（`version_no`、`head_plan_id`、最新版的生成时间 `cutoff_at` 与相对上一版的宿主差异 `diff`（第 0 版为 `null`）、`state`、`mode`、`basis`、`latest_review`（最近一次有效复查，含各条件的判定 `conditions`））；`agent-1` 单元带 `legacy: true`。另有 `chains`（已决定买入、等待成交的入场期链与持仓期链：第几版、最近复查的结论 / 判定方 / 时间 / 理由前 40 字、当日触发的信号）、持仓行的 `has_chain`（持仓所在单元有 `agent-2` 链，任何状态都算）与 `chain_state`（该链在 S 的状态：`holding`，或在持仓期间结束的状态如 `invalidated`；无链为 `null`），以及 `timeline_ready`（迁移 026 是否就绪）。
- API：`GET /api/workbench/agent-runs?since=`（运行列表与覆盖率 `coverage`）、`GET /api/workbench/agent-runs/{id}`（详情和全部工具调用）、`GET /api/workbench/agent-runs/{id}/reasoning`（推理阅读视图，见上）、`POST /api/workbench/agent-runs`（body `{ticker, signal_date}`；已有 `ok`/`pass` 返回 409，不是买入单元返回 400）、`POST /api/workbench/agent-runs/rerun`（重置尝试次数并重跑，见上）。
- 命令行：`python -m investment_assistant.analysis.agent run --ticker X --date YYYY-MM-DD`。
- 运行报告：`summary.agent`（`{runs, tokens, quota_exhausted}`）、`summary.sources.agent`、`summary.timings.agent`；`error` 状态的运行带脱敏后的 `detail`。
- 验证一次：跑完批处理后 `GET /api/workbench/agent-runs`，`coverage.missing` 应为空；任选一个买入单元，Agent 卡片上每个价位旁都有引用编号，点开能看到工具调用和哈希。

## 已知限制

- 模型层的请求形状（工具放置方式、被拒字段、`function_call` 回传、`usage` 键）按官方文档与 devkit 写成，等 [v2.8 spec §17](../specs/2026-10-29-v2.8-daily-workflow-design.md) 的真实核查结果确认；额度用尽的真实错误文本（C7）要等第一次出现后核对（看 `error` 运行的 `detail`）。
- 刷新时进程在「令牌端点已返回、数据库还没提交」之间被杀，旧 refresh token 已失效，下次刷新会得到 `refresh_token_reused` → `needs_login`，重新连接一次即可（概率很低）。
- 模型输出格式不保证稳定：带围栏的 JSON 和数字字符串已做兼容；JSON 里夹带说明文字的，作废为 `malformed`。
- Agent 价位来自模型输出，不构成投资建议；证据状态仍是「未验证」。目前不设晋升机制，只观察，不安排用 Agent 替换主计划。
- 无链持仓与同标的新入场链并存时，`holding_status` 按标的一行（全部未平批次），`execution.chain_buys` 按链，两边的股数可能不同（v2.9 R19）。
- 分次买入使加权成本上移后，`reached_1r` 可能由真变假，最长持有的时间止损随之重新生效（v2.9 R19）。
- 已并入的链当天被宿主作废时，这次触发不会补开新链，要等下一次触发（计划表给该标的出一行规则基线，标「所并入的链今日作废」）；退出期的链不再复查，「重新复查」返回 409 `chain_exiting`。
- 备注（`plan_notes`）不进任何 Agent 提示词；想让 Agent 参考要另写「我的补充」。
