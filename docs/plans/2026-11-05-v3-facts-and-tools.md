# v3 第一阶段：文档归档、Pi 可行性验证、事实层与工具面 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付 v3 的地基：旧文档归档、Pi 无人值守的可行性结论、一条不调模型就能产出「规则名单草稿」的事实任务，以及 agent 接触数据的唯一途径——一个 MCP 工具服务。

**Architecture:** 新代码放在两个新包里：`investment_assistant/facts/`（采集驱动、行情因子、排序、草稿、v3 的 SQL）和 `investment_assistant/toolsurface/`（工具函数、宿主校验、stdio 上的 MCP 子集）。它们只依赖将被保留的下层模块；旧的上层包（`workflow`、`views`、`api` 等）原样不动，新旧并行。采集并发通过给 `data/registry.run_adapter` 加一个默认为 1 的 `workers` 参数实现，旧调用方行为不变。

**Tech Stack:** Python 3.11 / psycopg / PostgreSQL（迁移 SQL）/ pytest（离线 + 一次性库 55433）；Pi 1.5.0（只在 Task 2 使用）。不新增 Python 依赖。

**Spec:** `docs/specs/2026-11-05-agent-first-rebuild-design.md`（下文「spec §x」）。本 plan 覆盖第 0 步、第 1 步和文档归档；第 2 步（每日简报）、第 3 步（页面服务与对话）及以后另写 plan。spec §15 是获批后追加的增补（保留页面入口、运行时可切换），与正文冲突处以 §15 为准；步骤编号按 §15.6。

## Global Constraints

- 主干开发：直接在 `main` 上提交；每个 Task 结束一个或几个提交，前缀 `feat:` / `refactor:` / `test:` / `docs:`，末尾加 `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`。不 push、不打 tag。
- 子代理：实现用 `sonnet`，审查用 `opus`；每个 Task 一轮审查，修复后由编排者看 diff 与相关测试确认，不派复审。全量 code review 只在 Task 7 做一次。
- 测试分级：每个 Task 只跑本 Task 列出的测试文件。离线全量 `pytest` 与一次性库全量 `pytest` 只在 Task 7 各跑一次。前端不在本 plan 改动，不跑前端测试。
- 离线测试命令前缀：`env -u INVESTMENT_ASSISTANT_DATABASE_URL -u INVESTMENT_ASSISTANT_TEST_DATABASE_URL -u TEST_DATABASE_URL .venv/bin/python -m pytest`。库测试用一次性库（端口 55433），设 `INVESTMENT_ASSISTANT_TEST_DATABASE_URL`，绝不指向真实库。
- **真实库不动**：迁移 032 在真实库的执行、真实网络的采集试跑，都在 Task 7 里先停下来请用户单独授权，执行前先 `pg_dump` 备份。
- **新代码的 import 边界**：`facts/` 与 `toolsurface/` 只能 import `core.{tx,dbutil,constants,redact,texts,knowledge}`、`data.*`、`derivation.*`、`analysis.{rules,screen_rules,regime,pool}`、`execution.{plan_levels,entry_rules,positions}`、`db`、`config`，以及彼此（`toolsurface` 可 import `facts`，反向不行）。不得 import `analysis.repo`、`execution.repo`、`workflow`、`views`、`api`、`services`、`tasks`、`notify`、`review`、`optimize`、`analysis.agent`、`core.llm`。
- 不改旧口径：规则 v1、`screen-1`、`regime-2`、`plan_levels` 的算术原样调用，不改其代码。
- 不定义名为 `connection`、`_tx`、`read_only_tx` 的函数，也不定义名为 `Source`、`Adapter`、`Tally` 的类（仓库守护测试只允许它们各有一处定义）。
- spec 的固定取值（逐字）：前 N 只 `top_n = 5`；档位四个 `favorable` / `neutral` / `unfavorable` / `insufficient`，中文 `有利` / `中性` / `不利` / `样本不足`；结论三个 `confirm` / `veto` / `unknown`；模式两个 `brief` / `chat`；草稿页脚必须含「规则未经验证，不构成投资建议」；任何面向用户的文本不得出现「降级」。
- 本 plan 和新代码里不要写出模型提供方的具体名字（仓库有一条守护测试禁止某个旧提供方的名字出现在受跟踪文件里）。
- 文档：v3 的运维说明写在 `docs/operations/`；冻结的 spec / plan / 验收记录只修失效链接，不改正文。

## 与 spec 的三处出入

1. **概念卡不在本 plan 归档**。spec §10 写的是归档在第一个 Task 做，含 89 张旧概念卡。但旧系统还在运行，它的知识页和测试（钉死 129 张卡）仍在加载这些卡。概念卡的归档推迟到 spec 第 5 步，和旧代码一起删。
2. **v3 的每日采集不含 `sec_text` 族**（申报原文下载）。本 plan 没有任何工具读原文，采了也没人用。申报列表（`sec_filings`）照采。
3. **对话模式的三个工具（记成交、记本金、补数据）在本 plan 一并实现**。spec 把对话追问排在第 3 步，但这三个工具属于工具面这一层，且验收 9（记录本金后能算出股数）需要记本金的工具。

## Review Focus

1. 入选股票没有行业归类，或所在行业组成员不足 3 只：档位应为 `insufficient`，排序与理由行照常产出，不除零、不抛异常 → Task 4 `test_v3_factors.py::test_unclassified_and_small_groups_are_insufficient`。
2. 当天没有任何股票入选：草稿写「今天没有入选的股票。」，前 N 只为空，`brief` 模式下保存结论与计划对任何标的都返回 `out_of_scope` → Task 4 `test_v3_draft.py::test_no_candidates`、Task 6 `test_v3_tools_plan.py::test_brief_scope_empty_top_n`。
3. 基准 SPY 或 QQQ 当期日线缺失（节假日判断错位、代理故障）：写一条带原因的运行记录和只有原因的草稿，不产出名单，命令退出码 1，没有异常栈 → Task 5 `test_v3_job.py::test_benchmark_missing_writes_error_run`。
4. 从未记录过本金：计划照常保存，股数为空并带「未提供账户资金，未算股数」；仓位上限没有余量时股数为 0 并带提示；两种情况都不报错 → Task 6 `test_v3_tools_plan.py::test_plan_without_equity_and_without_room`。
5. 模型传来的参数不合法（未知工具、多余参数、`lookback=100000`、小写或带空格的代码、不存在的代码）：返回错误信封并记一行调用记录，服务继续处理下一条 → Task 6 `test_v3_tools_read.py::test_bad_arguments_return_error_envelope`、`test_v3_jsonrpc.py::test_bad_lines_do_not_stop_the_loop`。

---

## 文件结构

| 文件 | 职责 | Task |
|---|---|---|
| `scripts/archive_v2_docs.py` | 一次性：移动 v2 文档并重写相对链接 | 1 |
| `docs/roadmap.md` | v3 路线：spec §10 各步与状态 | 1 |
| `scripts/pi_precheck/mini_mcp.py`、`scripts/pi_precheck/run_checks.py` | Pi 可行性验证：固定数据的最小 MCP 服务与逐项检查 | 2 |
| `docs/operations/pi-unattended.md` | 验证结论：可用的命令、事件名、失败信号、耗时与用量 | 2 |
| `migrations/032_v3_agent_first.sql` | v3 的七张表 | 3 |
| `investment_assistant/facts/repo.py` | v3 表的全部 SQL | 3 |
| `investment_assistant/config.py`（改） | `V3Config` | 3 |
| `investment_assistant/facts/factors.py` | 三个行情因子（纯函数） | 4 |
| `investment_assistant/facts/ranking.py` | 名单排序与理由行（纯函数） | 4 |
| `investment_assistant/facts/coverage.py` | 由拉取记录行推出覆盖率与句子（纯函数） | 4 |
| `investment_assistant/facts/holdings.py` | 持仓事实（纯函数） | 4 |
| `investment_assistant/facts/market.py` | 市场环境：区制快照 + 行业 ETF 表（纯函数） | 4 |
| `investment_assistant/facts/draft.py` | 草稿 Markdown（纯函数） | 4 |
| `investment_assistant/data/registry.py`（改） | `run_adapter(..., workers=1)` | 5 |
| `investment_assistant/facts/collect.py` | v3 的采集驱动 | 5 |
| `investment_assistant/facts/job.py`、`facts/__main__.py` | 事实任务编排与命令行 | 5 |
| `investment_assistant/toolsurface/env.py` | `ToolEnv` | 6 |
| `investment_assistant/toolsurface/validation.py` | 范围与价位校验（纯函数） | 6 |
| `investment_assistant/toolsurface/reads.py`、`planning.py`、`chat.py` | 工具函数 | 6 |
| `investment_assistant/toolsurface/dispatch.py` | 工具登记、调用、记账 | 6 |
| `investment_assistant/toolsurface/jsonrpc.py`、`__main__.py` | stdio 上的 MCP 子集与启动入口 | 6 |

## 执行顺序与依赖

```
Task 1（文档）  ─┐
Task 2（Pi 验证）─┤  互不依赖，可同时做
Task 3（迁移与仓储）─┬─► Task 5（采集与事实任务）─┐
Task 4（纯函数）  ──┘                            ├─► Task 7（收尾）
Task 3 ─────────────► Task 6（工具面）──────────┘
```

- **可以同时做**：Task 1、Task 2、Task 3、Task 4 的文件互不重叠。Task 5 与 Task 6 的文件也互不重叠，可同时做。
- **共享接口文件**：`investment_assistant/facts/repo.py`、`tests/conftest.py` 的 `v3_db` / `v3_tx`、`tests/test_layer_dependencies.py` 的登记都由 Task 3 负责；Task 4–6 只读不改。Task 4 的测试在 Task 3 登记新包之前跑不过分层守护，所以 Task 4 的收尾提交排在 Task 3 之后。
- **Task 2 需要用户到场**：在专用目录登录 Pi、选定模型只能用户自己做。Task 2 失败则停下整个 plan，回到用户面前（spec §10「第 0 步失败的退路」）。
- **Task 6 读 Task 2 的结论**：MCP 线上格式以 `docs/operations/pi-unattended.md` 里记录的、对 Pi 1.5.0 实测通过的写法为准。Task 2 未完成时，Task 6 先按本 plan 的协议子集实现并测试，Task 2 的结论出来后由编排者核对差异。

---

### Task 1: 文档归档与约定更新

**Files:**
- Create: `scripts/archive_v2_docs.py`、`docs/roadmap.md`
- Move: `docs/roadmap/` → `docs/archive/v2/roadmap/`；`docs/features/` → `docs/archive/v2/features/`；`docs/architecture.md` → `docs/archive/v2/architecture.md`（`docs/design-system.md` 不动：spec §15.1，保留下来的前端组件层仍以它为标准）
- Modify: `CLAUDE.md`、`README.md`、`docs/README.md`、`scripts/README.md`、`tests/test_no_zhipu_residue.py`，以及链接被改写的各 Markdown 文件
- Test: `tests/test_docs_links.py`、`tests/test_no_zhipu_residue.py`、`tests/test_knowledge.py`、`tests/test_knowledge_catalog.py`、`tests/test_roadmap_export.py`

**Interfaces:**
- Consumes: 无。
- Produces: `docs/roadmap.md`（Task 7 更新其中的状态）；`docs/archive/v2/` 的目录结构（保持原有相对结构，被移动文件之间的互链不变）。

- [ ] **Step 1: 写 `scripts/archive_v2_docs.py`**

  `MOVES: dict[str, str]` 即上面三项。`main()` 做三件事：对每项 `git mv`；遍历所有受 git 跟踪的 `*.md`，对每个 Markdown 链接 `](目标#锚点)`（跳过 `http`、`mailto`、纯锚点），用文件**移动前**的路径解析出目标，把目标和文件自身都按 `MOVES` 映射，再按文件**移动后**的路径重新算相对路径并写回；最后打印改了哪些文件、各几处。只改链接目标，不动其他文字。脚本幂等：第二次运行不再有改动。

- [ ] **Step 2: 运行脚本并核对**

  Run: `.venv/bin/python scripts/archive_v2_docs.py`，然后 `git status --short | head -40`
  Expected: 三项显示为重命名；被改写的文件只有链接行变化（`git diff --stat` 抽查三个冻结的 spec，确认每处差异都在 `](...)` 内）。

- [ ] **Step 3: 新建 `docs/roadmap.md`**

  内容固定为：一句目标（取 spec §2 目标 1–3）；一张表列出 spec §15.6 的第 0–5 步（第 3 步是「页面服务与对话」），列为「步 / 内容 / 状态」，状态初值全部「未开始」；指向 spec、本 plan、`docs/archive/v2/roadmap/roadmap.md` 的三个链接；一句「本文件直接修改维护」。

- [ ] **Step 4: 改写约定与索引**

  - `CLAUDE.md`：把「Roadmap 记录」那一条整条替换为：`- **Roadmap 记录。** roadmap 是仓库里的 `docs/roadmap.md`，直接修改。v2 的路线与功能文档已归档到 `docs/archive/v2/`，只读。spec / plan 仍在 `docs/specs/`、`docs/plans/`。`
  - `docs/README.md`：「系统现在是什么样」与「方向与历史」两节改为先列 v3（roadmap、spec、plan、`operations/`），再列「v2（已归档）」指向 `archive/v2/`；维护规则表里 roadmap 一行改为「仓库文件 `docs/roadmap.md`，直接修改」。
  - `README.md`：开头的版本状态段落（v2.4–v2.10 各段）换成三句：v3 重做进行中并链接 `docs/roadmap.md`；旧系统（v2.15）在切换前照常运行；旧文档在 `docs/archive/v2/`。快速开始与测试两节不动。
  - `scripts/README.md`：加一行说明 `archive_v2_docs.py` 是一次性脚本。

- [ ] **Step 5: 让守护测试认可 v3 的 spec**

  `tests/test_no_zhipu_residue.py`：加常量 `V3_DOCS = ('docs/specs/2026-11-05-agent-first-rebuild-design.md',)`，`_allowed` 对其中路径返回 `True`；在 `test_allow_list_is_exactly_the_spec_list` 的「应放行」列表里加上这条路径。（这一步若已由编排者先行提交，则跳过。）

- [ ] **Step 6: 跑本 Task 的测试**

  Run: `<离线前缀> tests/test_docs_links.py tests/test_no_zhipu_residue.py tests/test_knowledge.py tests/test_knowledge_catalog.py tests/test_roadmap_export.py -q`
  Expected: 全部通过。再跑 `.venv/bin/python -m investment_assistant.core.knowledge check`，Expected: 无问题输出。

- [ ] **Step 7: Commit**

  ```bash
  git add -A docs scripts CLAUDE.md README.md tests/test_no_zhipu_residue.py
  git commit -m "docs: archive v2 roadmap and feature docs, start the v3 roadmap"
  ```

---

### Task 2: Pi 无人值守可行性验证（spec §10 第 0 步）

**Files:**
- Create: `scripts/pi_precheck/mini_mcp.py`、`scripts/pi_precheck/run_checks.py`、`docs/operations/pi-unattended.md`
- Modify: `scripts/README.md`
- Test: 无自动化测试；本 Task 的产物就是检查结果。

**Interfaces:**
- Consumes: 用户已完成的两件事（见 Step 1）。
- Produces: `docs/operations/pi-unattended.md`——Task 6 和下一份 plan（每日简报）以它记录的命令行、事件名和失败信号为准。

- [ ] **Step 1: 请用户准备专用配置目录（只能用户做）**

  请用户在终端执行并完成登录、选好模型：

  ```bash
  mkdir -p ~/.pi-ia && PI_CODING_AGENT_DIR=~/.pi-ia ~/.local/bin/pi
  ```

  在界面里用 `/login` 登录要用的提供方。要用自定义提供方时，由用户把它的定义文件放进 `~/.pi-ia/`。然后请用户告知要验证的提供方与模型 id。专用目录位置固定为 `~/.pi-ia`。

- [ ] **Step 2: 写 `scripts/pi_precheck/mini_mcp.py`**

  约 60 行、无依赖的 stdio 服务，换行分隔的 JSON-RPC。启动参数 `--session`、`--log`。处理 `initialize`（回显客户端请求的 `protocolVersion`，`capabilities` 为 `{"tools": {}}`）、`notifications/*`（不回复）、`ping`、`tools/list`、`tools/call`；其他方法回 `-32601`。两个工具：`get_price_bars(ticker)` 返回两根固定日线，最后一根 `close = 104.25`；`get_session()` 返回启动参数里的 `--session`。每收到一条请求向 `--log` 追加一行（方法名、工具名、参数）。

- [ ] **Step 3: 写 `scripts/pi_precheck/run_checks.py`**

  ```
  .venv/bin/python scripts/pi_precheck/run_checks.py --provider P --model M [--agent-dir ~/.pi-ia] [--launchd]
  ```

  每次调用 Pi 都用同一套做法：新建临时工作目录，写入 `.pi/mcp.json`（一个名为 `ia` 的服务，`command` 为 `.venv/bin/python` 的绝对路径，`args` 指向 `mini_mcp.py` 并带 `--session 2026-11-04 --log <文件>`，`"exposure": "direct"`）；环境只含 `HOME`、`PATH=$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin`、从当前环境继承的代理变量、`PI_CODING_AGENT_DIR`、`PI_SKIP_VERSION_CHECK=1`；`stdin` 关闭；进程超时 180 秒。命令行：

  ```
  pi --mode json --no-session --approve --no-extensions -e builtin:mcp --no-skills --no-prompt-templates --no-themes --no-context-files --tools 'mcp__ia__*' --provider P --model M "<提示词>"
  ```

  逐项检查，输出一份 JSON（每项 `{ok, detail}`）并以全部通过与否决定退出码：

  | 检查 | 做法 | 通过条件 |
  |---|---|---|
  | `binary` | 最小环境下 `pi --version` | 退出码 0，输出版本号 |
  | `auth` | `pi auth check --provider P --json` | 退出码 0 |
  | `mcp_roundtrip` | 提示词：调用 get_price_bars 查 AAPL，只回复最后一根的 close | 最终回复含 `104.25`；服务日志里有 `tools/call` 且 `get_session` 可返回 `2026-11-04` |
  | `tool_allowlist` | 读上一项的事件流 | 所有工具执行事件的工具名都以 `mcp__ia__` 开头 |
  | `no_builtin_effects` | 临时目录放 `canary.txt`（随机串）；提示词要求读它并访问 `https://example.com`，做不到就说做不到 | 最终回复不含随机串、不含 `Example Domain`；没有非 `mcp__ia__` 的工具事件 |
  | `context_isolation` | 临时目录的父目录放 `AGENTS.md`、临时目录放 `CLAUDE.md`，各含一个 `CANARY-` 开头的随机词；提示词要求原样输出上下文里所有 `CANARY-` 开头的词，没有就回复 `NONE` | 回复不含任何一个随机词；`pi mcp list --json` 只列出 `ia` 一个服务 |
  | `usage` | 累加事件流里每条助手消息的用量 | 总 token 数大于 0 |
  | `failure_detected` | 用一个不存在的模型 id 再跑 `mcp_roundtrip` | 脚本能判出失败；`detail` 写明靠的是哪个信号（退出码、最后一条助手消息的停止原因、还是没有工具调用） |
  | `session_continuation` | 同一个工作目录里连跑两次，都不带 `--no-session`，改带 `--session-dir <临时目录>/sessions --session-id precheck-1`：第一次让它记住一个随机词并只回复 OK，第二次问那个词是什么 | 第二次的最终回复含这个随机词（页面上的多轮对话靠它续接） |
  | `launchd`（带 `--launchd` 时） | `launchctl submit -l local.ia.pi-precheck -- /bin/sh -c '…'` 跑一次 `mcp_roundtrip`，用标记文件保证只跑一次，最多等 180 秒后 `launchctl remove` | 输出文件里的最终回复含 `104.25` |

  事件名与字段以 Pi 自带文档 `~/.pi/agent/install/releases/1.5.0/node_modules/@earendil-works/pi-coding-agent/docs/json.md` 为准，不凭记忆写。脚本不读取任何含凭据的文件，输出里不打印代理地址和令牌。

- [ ] **Step 4: 运行检查**

  Run: `.venv/bin/python scripts/pi_precheck/run_checks.py --provider <用户给的> --model <用户给的> --launchd`
  Expected: 退出码 0，十项全部 `ok: true`。
  **任何一项不通过：停止本 plan，把 JSON 结果原样交给用户，不自行换运行时、不绕过。**

- [ ] **Step 5: 写 `docs/operations/pi-unattended.md`**

  记录：验证日期与 Pi 版本；实测通过的完整命令行（提供方与模型写成占位符）；`.pi/mcp.json` 的写法；看到的事件名；成败判定用的信号；一次调用的耗时与 token 用量；十项检查的结果表；尚未验证的事项。在 `scripts/README.md` 加一行说明这两个脚本的用途。

- [ ] **Step 6: Commit**

  ```bash
  git add scripts/pi_precheck scripts/README.md docs/operations/pi-unattended.md
  git commit -m "docs: Pi 1.5.0 unattended feasibility check and results"
  ```

---

### Task 3: 迁移、仓储与配置

**Files:**
- Create: `migrations/032_v3_agent_first.sql`、`investment_assistant/facts/__init__.py`、`investment_assistant/facts/repo.py`、`investment_assistant/toolsurface/__init__.py`、`tests/test_v3_repo.py`、`tests/test_v3_config.py`、`tests/test_v3_import_boundary.py`
- Modify: `investment_assistant/config.py`、`tests/test_layer_dependencies.py:7-12`、`tests/conftest.py`

**Interfaces:**
- Consumes: `core.dbutil._one` / `_all`；`core.tx.connection`（测试里被替换为测试连接）。
- Produces（Task 4–6 使用）:

  ```python
  # investment_assistant/config.py
  @dataclass(frozen=True)
  class V3Config:
      top_n: int = 5
      collect_workers: int = 6
      sec_workers: int = 3
      replay_sessions: int = 20
      hold_sessions: int = 5
      min_triggers: int = 10
      win_hi: float = 0.60
      win_lo: float = 0.40
      min_group: int = 3
      sector_etfs: tuple[str, ...] = ('QQQ', 'SMH', 'XLK', 'XLV', 'XLY', 'XLP', 'XLE', 'XLF', 'XLI', 'XLB', 'XLU', 'XLRE', 'XLC')
      briefs_dir: str = ''
  # AssistantConfig 增加字段 v3: V3Config；配置文件顶层键 "v3"
  def briefs_dir(cfg: AssistantConfig) -> Path   # cfg.v3.briefs_dir，空则 $INVESTMENT_ASSISTANT_RUNTIME_BASE/briefs，再空则 .local/v3/briefs

  # investment_assistant/facts/repo.py —— 每个函数第一个参数都是 conn，返回 dict 行
  def insert_run(conn, row: dict) -> dict
  def insert_candidates(conn, run_id: str, rows: list[dict]) -> int
  def latest_run(conn, session: date | None = None) -> dict | None      # 该交易日最新一条；不给交易日则全表最新
  def candidates(conn, run_id: str) -> list[dict]                        # 按 rank 升序
  def upsert_verdict(conn, row: dict) -> dict                            # (session, ticker) 唯一，重复则覆盖
  def verdicts(conn, session: date) -> dict[str, dict]                   # 以 ticker 为键
  def upsert_plan(conn, row: dict) -> dict                               # (session, ticker) 唯一，重复则覆盖
  def plans(conn, session: date) -> list[dict]
  def latest_plan(conn, ticker: str) -> dict | None
  def insert_trade(conn, row: dict) -> dict                              # request_id 重放返回原行
  def list_trades(conn, ticker: str | None = None) -> list[dict]         # 按 executed_at 升序
  def insert_equity(conn, row: dict) -> dict                             # request_id 重放返回原行
  def latest_equity(conn) -> dict | None                                 # as_of 最新，其次 id 最新
  def next_seq(conn, agent_session: str) -> int
  def insert_tool_call(conn, row: dict) -> dict
  def tool_call_refs(conn, agent_session: str) -> set[str]               # {'call:1', 'call:2', ...}

  # tests/conftest.py
  V3_MIGRATIONS = (*WORKBENCH_MIGRATIONS, '032_v3_agent_first.sql')
  @pytest.fixture
  def v3_db(): ...          # 同 workbench_db，多一份 032
  @pytest.fixture
  def v3_tx(v3_db, monkeypatch): ...   # 把 core_tx.connection 换成返回 v3_db 的上下文管理器（照 tests/test_api_features.py 的 api_db）
  ```

- [ ] **Step 1: 写迁移 `migrations/032_v3_agent_first.sql`**

  ```sql
  CREATE TABLE IF NOT EXISTS v3_runs (
    id BIGSERIAL PRIMARY KEY, run_id TEXT NOT NULL UNIQUE, session DATE NOT NULL,
    trigger TEXT NOT NULL CHECK (trigger IN ('scheduled', 'manual_batch')),
    status TEXT NOT NULL CHECK (status IN ('ok', 'error')), error TEXT,
    market JSONB NOT NULL DEFAULT '{}', factors JSONB NOT NULL DEFAULT '{}',
    coverage JSONB NOT NULL DEFAULT '{}', holdings JSONB NOT NULL DEFAULT '[]',
    draft_md TEXT NOT NULL, started_at TIMESTAMPTZ NOT NULL, finished_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK ((status = 'error') = (error IS NOT NULL)));
  CREATE INDEX IF NOT EXISTS idx_v3_runs_session ON v3_runs (session, created_at DESC);

  CREATE TABLE IF NOT EXISTS v3_candidates (
    id BIGSERIAL PRIMARY KEY, run_id TEXT NOT NULL REFERENCES v3_runs(run_id), session DATE NOT NULL,
    ticker TEXT NOT NULL, rank INT NOT NULL CHECK (rank >= 1),
    screen_group TEXT NOT NULL CHECK (screen_group IN ('trigger', 'setup')),
    trigger_rules JSONB NOT NULL DEFAULT '[]', rs DOUBLE PRECISION, atr_pct DOUBLE PRECISION,
    setup_tier TEXT NOT NULL, industry TEXT, industry_tier TEXT NOT NULL,
    reason TEXT NOT NULL, detail JSONB NOT NULL DEFAULT '{}',
    UNIQUE (run_id, ticker), UNIQUE (run_id, rank));

  CREATE TABLE IF NOT EXISTS v3_verdicts (
    id BIGSERIAL PRIMARY KEY, session DATE NOT NULL, ticker TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('confirm', 'veto', 'unknown')),
    reasons TEXT NOT NULL, counter TEXT NOT NULL DEFAULT '', refs JSONB NOT NULL DEFAULT '[]',
    origin TEXT NOT NULL CHECK (origin IN ('brief', 'chat')), agent_session TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (session, ticker));

  CREATE TABLE IF NOT EXISTS v3_plans (
    id BIGSERIAL PRIMARY KEY, session DATE NOT NULL, ticker TEXT NOT NULL,
    origin TEXT NOT NULL CHECK (origin IN ('brief', 'chat')),
    entry_type TEXT NOT NULL, entry_low NUMERIC(18,6) NOT NULL, entry_high NUMERIC(18,6) NOT NULL,
    stop NUMERIC(18,6) NOT NULL, target1 NUMERIC(18,6) NOT NULL,
    levels_source TEXT NOT NULL CHECK (levels_source IN ('host', 'agent')),
    max_hold_sessions INT NOT NULL, valid_from DATE NOT NULL, valid_until DATE NOT NULL,
    shares INT, sizing JSONB NOT NULL DEFAULT '{}', warnings JSONB NOT NULL DEFAULT '[]',
    thesis TEXT NOT NULL, invalidation TEXT NOT NULL, counter TEXT NOT NULL DEFAULT '',
    refs JSONB NOT NULL DEFAULT '[]', agent_session TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (session, ticker), CHECK (stop < entry_low AND entry_low <= entry_high AND entry_high < target1));

  CREATE TABLE IF NOT EXISTS v3_trades (
    id BIGSERIAL PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, ticker TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')), quantity NUMERIC(18,6) NOT NULL CHECK (quantity > 0),
    price NUMERIC(18,6) NOT NULL CHECK (price > 0), fee NUMERIC(18,6) NOT NULL DEFAULT 0 CHECK (fee >= 0),
    executed_at TIMESTAMPTZ NOT NULL, plan_id BIGINT REFERENCES v3_plans(id), note TEXT NOT NULL DEFAULT '',
    voided_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());

  CREATE TABLE IF NOT EXISTS v3_equity (
    id BIGSERIAL PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, as_of DATE NOT NULL,
    equity NUMERIC(18,2) NOT NULL CHECK (equity > 0), note TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp());

  CREATE TABLE IF NOT EXISTS v3_tool_calls (
    id BIGSERIAL PRIMARY KEY, agent_session TEXT NOT NULL, mode TEXT NOT NULL CHECK (mode IN ('brief', 'chat')),
    session DATE NOT NULL, seq INT NOT NULL CHECK (seq >= 1), tool TEXT NOT NULL,
    args JSONB NOT NULL DEFAULT '{}', result_sha256 TEXT, error TEXT,
    called_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), UNIQUE (agent_session, seq));
  ```

- [ ] **Step 2: 登记新包、加测试夹具**

  `tests/test_layer_dependencies.py`：`TOP = ['workflow', 'views', 'facts']`、`EDGE = ['api', 'services', 'tasks', 'notify', 'toolsurface']`。`tests/conftest.py` 加 `V3_MIGRATIONS`、`v3_db`、`v3_tx`。两个包各建一个空的 `__init__.py`（`investment_assistant/facts/` 下若有遗留的 `__pycache__`，删掉）。

- [ ] **Step 3: 写失败的测试**

  `tests/test_v3_import_boundary.py::test_v3_packages_import_only_kept_modules`：用 `ast` 遍历 `investment_assistant/facts/` 与 `investment_assistant/toolsurface/` 下每个 `.py`（含函数体内的 import），每个 `investment_assistant.*` 的 import 必须以 Global Constraints 列出的前缀之一开头，且 `facts/` 不得 import `investment_assistant.toolsurface`。

  `tests/test_v3_config.py`：

  ```python
  def test_defaults():
      cfg = load_config(tmp_path / 'missing.json')
      assert cfg.v3.top_n == 5 and cfg.v3.hold_sessions == 5 and cfg.v3.min_group == 3
      assert cfg.v3.sector_etfs[:3] == ('QQQ', 'SMH', 'XLK') and len(cfg.v3.sector_etfs) == 13

  def test_file_overrides_and_validation(tmp_path):
      # {"v3": {"top_n": 3, "win_hi": 0.7}} -> top_n == 3, win_hi == 0.7
      # 下列每个都 raise ValueError：top_n 0 或 21；collect_workers 7；sec_workers 5；
      # replay_sessions 4；hold_sessions 0；win_lo >= win_hi；min_group 1

  def test_briefs_dir(monkeypatch, tmp_path):
      # 配了 briefs_dir 用它；否则 $INVESTMENT_ASSISTANT_RUNTIME_BASE/briefs；都没有则 Path('.local/v3/briefs')
  ```

  `tests/test_v3_repo.py`（用 `v3_db`）：

  ```python
  def test_runs_and_candidates(v3_db):
      # 同一交易日插两条 run：latest_run(conn, session) 返回后插的；latest_run(conn) 同
      # insert_candidates 写 3 行后 candidates(run_id) 按 rank 1,2,3 返回，trigger_rules 读回为 list

  def test_verdict_and_plan_upsert(v3_db):
      # 同 (session, ticker) 存两次：表里只有一行，字段是第二次的，updated_at > created_at
      # verdicts(conn, session) 以 ticker 为键；latest_plan(conn, 'AAPL') 返回 session 最新的一份

  def test_trade_and_equity_idempotent(v3_db):
      # 同 request_id 插两次返回同一 id，表里一行
      # latest_equity 按 as_of 取最新；空表返回 None

  def test_tool_calls(v3_db):
      # next_seq 从 1 开始、插入后变 2；tool_call_refs == {'call:1'}
  ```

- [ ] **Step 4: 运行，确认失败**

  Run: `<离线前缀> tests/test_v3_config.py tests/test_v3_import_boundary.py -q`，再用一次性库跑 `tests/test_v3_repo.py -q`
  Expected: FAIL（`V3Config`、`facts.repo` 不存在）。

- [ ] **Step 5: 实现**

  `config.py`：`V3Config`、`AssistantConfig.v3`、`_config_from_dict` 在值为 dict 时解析 `"v3"`、`_validate_v3`（范围：`1 <= top_n <= 20`、`1 <= collect_workers <= 6`、`1 <= sec_workers <= 4`、`5 <= replay_sessions <= 60`、`1 <= hold_sessions <= 20`、`0 < win_lo < win_hi < 1`、`min_group >= 2`）、`briefs_dir(cfg)`。
  `facts/repo.py`：按 Interfaces 的签名实现，用 `core.dbutil._one` / `_all`；`insert_candidates` 的 `session` 取自该 `run_id` 对应的运行行，调用方不传；两个 upsert 用 `ON CONFLICT (session, ticker) DO UPDATE` 并把 `updated_at` 设为 `clock_timestamp()`；JSONB 列写入用 `core.dbutil._plan_json`。

- [ ] **Step 6: 运行，确认通过**

  Run: 同 Step 4，另加 `<离线前缀> tests/test_layer_dependencies.py tests/test_core_tx_single_definition.py tests/test_no_update_on_append_only_tables.py -q`
  Expected: 全部 PASS。

- [ ] **Step 7: Commit**

  ```bash
  git add migrations/032_v3_agent_first.sql investment_assistant/facts investment_assistant/toolsurface investment_assistant/config.py tests/conftest.py tests/test_layer_dependencies.py tests/test_v3_repo.py tests/test_v3_config.py tests/test_v3_import_boundary.py
  git commit -m "feat(v3): migration 032, facts repository and V3Config"
  ```

---

### Task 4: 纯函数层——行情因子、排序、覆盖率、持仓、市场环境、草稿

**Files:**
- Create: `investment_assistant/facts/{factors,ranking,coverage,holdings,market,draft}.py`
- Test: `tests/test_v3_factors.py`、`tests/test_v3_ranking.py`、`tests/test_v3_coverage.py`、`tests/test_v3_holdings.py`、`tests/test_v3_market.py`、`tests/test_v3_draft.py`

**Interfaces:**
- Consumes: `analysis.rules.evaluate(ticker, signal_date, bars, filings, position)`；`analysis.screen_rules.TRIGGER_RULES`；`analysis.regime.snapshot(*, session, pool_bars, etf_bars, spy_bars, macro_rows, macro_status=None)`；`derivation.indicators.breadth.sector_strength(etf_bars, spy_bars, session)`；`execution.positions.build(trades, *, splits=None)`；`data.calendar.add_trading_days(d, n)`；`core.texts.RULE_TEXT`；`config.V3Config`。日线字典的键：`id, price_date, open, high, low, close, adj_close, volume`。测试用的日线构造器从现有测试模块导入：`tests/test_workbench_rules.py` 的 `series`、`breakout`、`uptrend_with_dip`、`T`。
- Produces:

  ```python
  # facts/factors.py
  BUY_RULES = screen_rules.TRIGGER_RULES                 # ('trend_reclaim', 'volume_breakout')
  def trigger_rules_on(bars: list[dict], day: date) -> list[str]
  def setup_followthrough(bars_by_ticker: dict[str, list[dict]], *, session: date, cfg: V3Config) -> dict[str, dict]
      # {rule_id: {'n': int, 'wins': int, 'win_rate': float | None, 'mean_return': float | None, 'tier': str, 'window': [iso, iso]}}
  def industry_strength(bars_by_ticker: dict[str, list[dict]], spy_bars: list[dict], groups: dict[str, str | None],
                        *, session: date, cfg: V3Config) -> dict[str, dict]
      # {group: {'members': int, 'eligible': int, 'pct_above_50': float | None, 'median_rs20': float | None, 'rank': int | None, 'tier': str}}
  def regime_tier(label: str) -> str

  # facts/ranking.py
  TIER_ORDER = {'favorable': 0, 'neutral': 1, 'insufficient': 2, 'unfavorable': 3}
  TIER_TEXT = {'favorable': '有利', 'neutral': '中性', 'unfavorable': '不利', 'insufficient': '样本不足'}
  def rank_candidates(results: list[dict], *, setup: dict[str, dict], industry: dict[str, dict],
                      groups: dict[str, str | None]) -> list[dict]
      # 每行：ticker, rank, screen_group, trigger_rules, rs, atr_pct, setup_tier, industry, industry_tier, reason, detail

  # facts/coverage.py
  FAMILY_TEXT = {'bars': '日线', 'news': '资讯', 'sec_filings': 'SEC 申报', 'earnings': '财报日', 'fred': '宏观',
                 'universe': '筛选范围', 'fundamentals': '基本面', 'sector': '行业归类'}
  def coverage(rows: list[dict]) -> dict
      # {'families': {family: {'total': int, 'ok': int, 'missing': [{'target', 'reason'}], 'skipped': str | None}}, 'missing_bars': [ticker]}
  def coverage_lines(cov: dict) -> list[str]

  # facts/holdings.py
  def holdings_view(trades: list[dict], *, closes: dict[str, float], stops: dict[str, Decimal | None],
                    splits: dict[str, list[tuple[date, Decimal]]] | None = None) -> list[dict]
      # 每行：ticker, quantity, avg_cost, last_close, pnl_pct, stop, to_stop_pct；持仓算不出时为 {'ticker', 'error': 'position_invalid'}

  # facts/market.py
  def market_view(*, session: date, universe_bars: dict[str, list[dict]], etf_bars: dict[str, list[dict]],
                  spy_bars: list[dict], macro_rows: dict[str, list[dict]], macro_status: dict[str, str] | None = None) -> dict
      # regime.snapshot 的全部键，另加 'regime_tier'、'spy_ret20'、'etf_table': [{'etf', 'ret20', 'rs20', 'rs60', 'state'}]

  # facts/draft.py
  def render_draft(*, session: date, as_of: datetime, market: dict, candidates: list[dict], holdings: list[dict],
                   coverage: dict, top_n: int, error: str | None = None) -> str
  ```

- [ ] **Step 1: 写失败的测试**

  `tests/test_v3_factors.py`：

  ```python
  def test_trigger_rules_on_mirrors_screen():
      # breakout(2.0) 的最后一天 -> ['volume_breakout']；同一序列若当天同时有卖出候选 -> []
      # 平盘序列 -> []

  def test_setup_followthrough_counts_and_tiers():
      # 构造 12 只股票，各在同一天 D（= add_trading_days(T, -8)）放量突破，其后 5 根：8 只上涨 3%、4 只下跌 2%
      # cfg = V3Config(min_triggers=10)，session = T
      out = setup_followthrough(bars, session=T, cfg=cfg)['volume_breakout']
      assert (out['n'], out['wins']) == (12, 8) and out['win_rate'] == pytest.approx(8 / 12, abs=1e-4)
      assert out['mean_return'] == pytest.approx((8 * 0.03 - 4 * 0.02) / 12, abs=1e-4) and out['tier'] == 'favorable'
      # 同样数据把 min_triggers 设为 13 -> tier == 'insufficient'
      # 4 只上涨、8 只下跌 -> 'unfavorable'；7 只上涨且均值为正、win_hi=0.6 -> 58% -> 'neutral'

  def test_setup_followthrough_window_and_missing_forward_bar():
      # 触发日在 add_trading_days(T, -4)（持有期没走完）-> 不计入
      # 触发日在窗口最早一天之前 -> 不计入
      # 触发后只有 3 根后续日线的股票 -> 不计入，也不抛异常
      # 没有任何触发时每条 BUY_RULES 都在结果里：n == 0, win_rate is None, tier == 'insufficient'

  def test_industry_strength_ranks_groups():
      # 6 个行业组各 3 只：站上 50 日线的占比分别为 3/3、3/3、2/3、1/3、0/3、0/3，相对强弱依次递减
      # -> 前两组 'favorable'，中间两组 'neutral'，后两组 'unfavorable'；rank 为 1..6

  def test_unclassified_and_small_groups_are_insufficient():
      # 只有 2 只成员的组 -> tier 'insufficient'、rank None、不参与其他组的名次
      # groups 里值为 None 的股票不形成组
      # 合格组不足 3 个时，合格组全部 'neutral'
      # 某只股票不足 21 根日线 -> 仍计入站上均线的统计（够 50 根才算 eligible），相对强弱跳过它

  def test_regime_tier():
      assert [regime_tier(x) for x in ('risk_on', 'neutral', 'risk_off', 'insufficient', 'other')] == \
          ['favorable', 'neutral', 'unfavorable', 'insufficient', 'insufficient']
  ```

  `tests/test_v3_ranking.py`：

  ```python
  def test_order_and_reason():
      # 5 只入选：A、B 触发（volume_breakout），C 触发（trend_reclaim），D、E 形态组
      # setup: volume_breakout favorable {n: 19, wins: 13, mean_return: 0.027}，trend_reclaim unfavorable {n: 12, wins: 3, mean_return: -0.044}
      # industry: 医药化工 favorable、电子 unfavorable；A 在电子 rs 0.50，B 在医药化工 rs 0.35，C 在医药化工 rs 0.90
      out = rank_candidates(results, setup=setup, industry=industry, groups=groups)
      assert [r['ticker'] for r in out] == ['B', 'A', 'C', 'D', 'E'] and [r['rank'] for r in out] == [1, 2, 3, 4, 5]
      assert out[0]['reason'] == '放量突破 · 形态近期有利（19 次 13 涨，+2.7%）· 医药化工 有利 · RS 0.35'
      assert out[3]['reason'].startswith('形态待触发 · ') and out[3]['setup_tier'] == 'insufficient'

  def test_unselected_rows_dropped_and_ties_by_ticker():
      # selected 为 False 的行不出现；档位与 rs 全相同的两只按代码字母序

  def test_two_trigger_rules_use_the_better_tier_and_unknown_industry():
      # trigger_rules == ['trend_reclaim', 'volume_breakout'] -> setup_tier 取较好的一条，理由里规则名用「、」连接
      # groups[ticker] 为 None -> industry None，industry_tier 'insufficient'，理由里写「未归类 样本不足」
      # n == 0 的规则 -> 理由里写「形态近期样本不足（0 次）」

  def test_deterministic():
      # 输入行顺序打乱后结果完全相同
  ```

  `tests/test_v3_coverage.py`：

  ```python
  def test_coverage_merges_rows_and_lists_missing():
      # 两条 bars 行（每日 + 补采）合并：total、ok 相加，missing 按 target 去重
      # bars 行里 8 个失败目标 -> cov['missing_bars'] 是这 8 个代码（不含 SPY 之外的 ETF 也照列）
      lines = coverage_lines(cov)
      assert any(l.startswith('日线：130/138，缺 ') for l in lines)
      assert not any('降级' in l or 'degraded' in l or 'partial' in l for l in lines)

  def test_whole_call_failure_and_skip():
      # failures 里有 {'target': '*'} -> 该族一行「SEC 申报：整族未取到：<原因>」
      # status 'skipped' 且 detail.reason 有值 -> 「宏观：未拉取：<原因>」
      # 全部成功的族 -> 「资讯：8/8」
  ```

  `tests/test_v3_holdings.py`：

  ```python
  def test_holdings_view():
      # 买 10 股 @100、再买 10 股 @110，最新收盘 120，止损 99 -> quantity 20, avg_cost 105, pnl_pct ≈ 0.1429, to_stop_pct == pytest.approx(0.175)
      # 已全部卖出的股票不出现；被作废（voided_at 非空）的成交不计
      # 没有止损 -> stop None, to_stop_pct None；没有最新收盘 -> last_close None, pnl_pct None
      # 卖出数量超过持仓 -> 该股票一行 {'ticker': 'X', 'error': 'position_invalid'}，其他股票照常
      # 空成交列表 -> []
  ```

  `tests/test_v3_market.py`：

  ```python
  def test_market_view_adds_etf_table():
      # 3 只 ETF，其中一只日线不足 61 根 -> 它的 rs20 / rs60 / state 为 None，排在表尾
      # 其余按 rs20 从高到低；'regime_tier' 与 regime_tier(label) 一致；'spy_ret20' 为 SPY 20 日涨跌幅
  ```

  `tests/test_v3_draft.py`：

  ```python
  def test_sections_and_footer():
      text = render_draft(session=date(2026, 11, 4), as_of=AS_OF, market=MARKET, candidates=CANDS, holdings=[], coverage=COV, top_n=5)
      for heading in ('# 草稿 · 2026-11-04', '## 市场环境', '## 适合交易什么（规则名单）', '## 持仓', '## 今天下不了结论的'):
          assert heading in text
      assert '规则未经验证，不构成投资建议' in text and '没有持仓记录。' in text and '降级' not in text
      # 前 5 只的行以「→ 进入二次确认」结尾，第 6 只起没有；每行以「{rank}. {ticker} · {reason}」开头

  def test_no_candidates():
      # candidates == [] -> 含「今天没有入选的股票。」，不含「→ 进入二次确认」

  def test_error_draft():
      # error='基准日线缺失：SPY' -> 只有标题、「今天没有草稿：基准日线缺失：SPY」和页脚，没有其他小节
  ```

- [ ] **Step 2: 运行，确认失败**

  Run: `<离线前缀> tests/test_v3_factors.py tests/test_v3_ranking.py tests/test_v3_coverage.py tests/test_v3_holdings.py tests/test_v3_market.py tests/test_v3_draft.py -q`
  Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现六个模块**

  签名见 Interfaces。测试没有钉死、需要照此实现的规则：

  - `trigger_rules_on`：`out = rules.evaluate('', day, bars, [], None)`；若有 `action == 'sell'` 的候选返回 `[]`；否则返回 `action == 'buy'` 且 `rule_id in BUY_RULES` 的规则 id（排序）。与 `screen_rules.evaluate` 判定触发组的条件相同。
  - `setup_followthrough`：信号日为截至 `add_trading_days(session, -cfg.hold_sessions)`（含）的 `cfg.replay_sessions` 个交易日。对每只股票每个信号日 D：取 `price_date <= D` 的日线，末根不是 D 则跳过；D 之后第 `hold_sessions` 根日线不存在则跳过；收益 = 该根 `adj_close` ÷ D 的 `adj_close` − 1。档位：`n < min_triggers` 为 `insufficient`；`win_rate >= win_hi` 且 `mean_return > 0` 为 `favorable`；`win_rate <= win_lo` 或 `mean_return < 0` 为 `unfavorable`；其余 `neutral`。`win_rate`、`mean_return` 四舍五入到 4 位小数。
  - `industry_strength`：股票在 `session` 有日线且有 50 根以上才算 eligible；站上 50 日线 = 末根 `adj_close` 大于最近 50 根均值；`rs20` = 股票 20 日涨跌幅 − SPY 20 日涨跌幅（需 21 根）。组的 eligible 数 ≥ `min_group` 才参与排名，排名键 `(-pct_above_50, -median_rs20, 组名)`。设合格组数为 k、`cut = ceil(k / 3)`：`rank <= cut` 为 `favorable`，`rank > k - cut` 为 `unfavorable`，其余 `neutral`；`k < 3` 时全部 `neutral`。
  - `rank_candidates`：只取 `selected` 的行。排序键 `(0 if group == 'trigger' else 1, TIER_ORDER[setup_tier], TIER_ORDER[industry_tier], -rs, ticker)`。形态组的 `setup_tier` 固定为 `insufficient`。多条触发规则取档位较好的一条，同档取 `n` 大的。理由行格式逐字如测试所示：涨跌幅用 `f'{x:+.1%}'`，相对强弱用 `f'{rs:.2f}'`，规则名取 `RULE_TEXT`。`detail` 为 `{'setup': 该规则的统计或 None, 'industry': 该组的统计或 None, 'screen_rank': 原名次}`。
  - `coverage`：输入是 `Tally.row()` 的字典（键 `family, status, target_count, ok_count, failures, detail`）。同族多行相加。`coverage_lines` 每族一行，格式见测试；缺失目标最多列 12 个，超出写「等 N 个」。
  - `holdings_view`：按股票分组调 `positions.build`，`position` 为 None 的不输出；`pnl_pct = last_close / avg_cost - 1`；`to_stop_pct = (last_close - stop) / last_close`。按代码排序。
  - `render_draft`：小节顺序与标题逐字如测试。市场环境一节写区制标签与档位、广度两个占比、新高新低数、SPY 20 日涨跌幅和行业 ETF 表（前 3 与后 3）。页脚：`数据截至 {session} 收盘（采集于北京时间 {as_of:%Y-%m-%d %H:%M}）。规则未经验证，不构成投资建议。`

- [ ] **Step 4: 运行，确认通过**

  Run: 同 Step 2，另加 `tests/test_v3_import_boundary.py`
  Expected: 全部 PASS。

- [ ] **Step 5: Commit**

  ```bash
  git add investment_assistant/facts/factors.py investment_assistant/facts/ranking.py investment_assistant/facts/coverage.py investment_assistant/facts/holdings.py investment_assistant/facts/market.py investment_assistant/facts/draft.py tests/test_v3_factors.py tests/test_v3_ranking.py tests/test_v3_coverage.py tests/test_v3_holdings.py tests/test_v3_market.py tests/test_v3_draft.py
  git commit -m "feat(v3): market factors, rule ranking, coverage, holdings and draft rendering"
  ```

---

### Task 5: 采集并发与事实任务

**Files:**
- Modify: `investment_assistant/data/registry.py:39-115`
- Create: `investment_assistant/facts/collect.py`、`investment_assistant/facts/job.py`、`investment_assistant/facts/__main__.py`
- Test: `tests/test_data_registry_workers.py`、`tests/test_v3_collect.py`、`tests/test_v3_job.py`；回归 `tests/test_data_registry.py`

**Interfaces:**
- Consumes: Task 3 的 `facts.repo`、`V3Config`、`briefs_dir`、`v3_db` / `v3_tx`；Task 4 的全部纯函数。现有接口：
  - `data.registry.get(family_id) -> Adapter`；`adapter.targets(conn, scope, *, explicit=None, facts=None, universe_loader=None) -> list[str]`
  - `data.adapter_types.TargetFacts(pool, holdings, paper_holdings, active_chains, sector_etfs, paper_accounts_ready, research_requests_ready)`
  - `data.adapter_support.adapter_deps_from_service(deps, *, transaction, config=None, sleep=None) -> AdapterDeps`
  - `data.collect.family_locks(family_ids)`、`collect.write_runs_with_ids(rows)`、`collect.wall_clock()`、`collect.FETCH_SLEEP`、`data.tally.Tally(family, job, targets)`、`Tally.row(*, trigger, run_id, session, started_at, finished_at, scope='family', ticker=None)`
  - `data.adapters.bars.latest_closed_session(now) -> date`；`data.adapters.universe.load() -> Universe`（`.tickers`）；`data.sources.edgar.COMPANY_TICKERS_TARGET`
  - `data.repo.read_bars(conn, ticker, end_date, *, cutoff_at, limit=260)`、`latest_earnings(conn, ticker, *, cutoff_at)`、`latest_sectors(conn, tickers, *, cutoff_at)`、`read_macro(conn, series_id, *, end_date, cutoff_at, limit=60)`、`split_events(conn, ticker, *, cutoff_at=None)`
  - `db.list_watchlist_items(conn)`、`analysis.pool.from_watchlist(items)`（`.tickers`；空或非法时抛 `ValueError`）
  - `analysis.screen_rules.evaluate(*, session, bars_by_ticker, benchmark_bars, earnings_by_ticker)`、`screen_rules.BenchmarkMissing`
  - `core.tx._tx()`、`core.tx.read_only_tx()`、`core.tx.Deps`、`core.tx.Unavailable`
- Produces:

  ```python
  # data/registry.py —— 新增一个关键字参数，默认行为不变
  def run_adapter(adapter, targets, *, scope, trigger, run_id, session, now, deps, features, tally=None,
                  observe=None, whole_call_target=None, workers: int = 1) -> Tally

  # facts/collect.py
  DAILY_FAMILIES = ('universe', 'bars', 'earnings', 'sector', 'fred', 'news', 'sec_filings', 'fundamentals')
  EXTRA_FAMILIES = ('news', 'sec_filings', 'fundamentals')
  YF_FAMILIES = ('bars', 'news', 'earnings')              # 用 cfg.v3.collect_workers
  SEC_FAMILIES = ('sec_filings', 'fundamentals', 'sector')  # 用 cfg.v3.sec_workers
  FEATURES = {'backup_sources': True}
  def new_run_id(now: datetime) -> str                     # 'v3-YYYYMMDDHHMMSS-<8 位十六进制>'
  def target_facts(conn, cfg: AssistantConfig) -> TargetFacts
  def collect_daily(*, session: date, now: datetime, run_id: str, trigger: str, cfg: AssistantConfig,
                    deps: AdapterDeps | None = None) -> list[dict]          # 每族一行 Tally.row()
  def collect_extra(tickers: list[str], *, session: date, now: datetime, run_id: str, trigger: str,
                    cfg: AssistantConfig, families: tuple[str, ...] = EXTRA_FAMILIES,
                    deps: AdapterDeps | None = None) -> list[dict]

  # facts/job.py
  def load_inputs(conn, *, session: date, now: datetime, cfg: AssistantConfig) -> dict
  def compute(inputs: dict, *, session: date, cfg: AssistantConfig) -> dict   # 键：market, screen, setup, industry, candidates
  def run_facts(*, now: datetime | None = None, trigger: str = 'manual_batch', collect: bool = True,
                cfg: AssistantConfig | None = None, deps: AdapterDeps | None = None) -> dict
      # {'run_id', 'session', 'status': 'ok' | 'error', 'error': str | None, 'candidates': int, 'draft_path': str}
  ```

  命令行：`python -m investment_assistant.facts run [--no-collect] [--trigger scheduled|manual_batch]`（打印上面的字典为 JSON；退出码 0 成功、1 为 `status == 'error'`、2 为数据库不可用）；`python -m investment_assistant.facts show [--session YYYY-MM-DD]`（打印该交易日最新一条运行的草稿）。

- [ ] **Step 1: 写 `run_adapter` 并发的失败测试**

  `tests/test_data_registry_workers.py`，用假适配器（照 `tests/test_data_registry.py` 里已有的假 `Source` / `make_adapter` 写法）：

  ```python
  def test_workers_book_every_target_once_in_input_order():
      # 12 个目标，fetch 各睡 20 毫秒；workers=4 与 workers=1 的 tally.row() 在去掉 elapsed_ms 后相等
      # workers=4 的总耗时小于 workers=1 的一半

  def test_one_failing_target_does_not_affect_others():
      # 第 5 个目标的 fetch 抛异常 -> 只有它记失败，其余 11 个成功

  def test_unavailable_propagates():
      # store 里抛 core.tx.Unavailable -> run_adapter 抛 Unavailable

  def test_whole_call_failure_books_star_once():
      # 第 3 个目标的错误带 target == whole_call_target -> '*' 记一次；它之后的目标不记成功也不记失败

  def test_default_does_not_create_a_pool(monkeypatch):
      # 把 registry 模块里的 ThreadPoolExecutor 换成一构造就抛异常的对象：不传 workers（或 workers=1）时调用照常成功
  ```

- [ ] **Step 2: 运行，确认失败**

  Run: `<离线前缀> tests/test_data_registry_workers.py -q`
  Expected: FAIL（`run_adapter() got an unexpected keyword argument 'workers'`）。

- [ ] **Step 3: 给 `run_adapter` 加 `workers`**

  把循环体里「取数 + 落库」的部分抽成只返回结果、不碰 `tally` 的内部函数；`tally.record`、`tally.detail.update`、`observe` 全部留在调用线程，按输入顺序处理结果。`workers == 1` 时逐个调用（与现在逐字等价）；`workers > 1` 时用 `ThreadPoolExecutor(max_workers=workers)` 的 `map`。处理到整族失败的那个结果时记 `'*'` 并停止处理后面的结果；`Unavailable` 原样抛出并取消尚未开始的任务。

- [ ] **Step 4: 运行，确认通过且旧测试不变**

  Run: `<离线前缀> tests/test_data_registry_workers.py tests/test_data_registry.py -q`
  Expected: 全部 PASS。

- [ ] **Step 5: Commit**

  ```bash
  git add investment_assistant/data/registry.py tests/test_data_registry_workers.py
  git commit -m "feat(data): optional worker pool in run_adapter, sequential by default"
  ```

- [ ] **Step 6: 写采集驱动与事实任务的失败测试**

  `tests/test_v3_collect.py`（用 `v3_tx`，适配器的取数函数全部用假的，经 `AdapterDeps` 的 `bar_fetcher` 等注入或直接替换 `registry.run_adapter`）：

  ```python
  def test_target_facts(v3_tx):
      # 关注列表 2 只 + v3_trades 里 1 只未平仓 -> pool 2 只、holdings 1 只
      # paper_holdings == ()、active_chains == ()、paper_accounts_ready is False、research_requests_ready is False
      # sector_etfs == cfg.v3.sector_etfs；关注列表为空时 pool == ()，不抛异常

  def test_collect_daily_runs_families_in_order_and_writes_rows(v3_tx, monkeypatch):
      # 记录每次 run_adapter 的 (family, len(targets), workers, whole_call_target)
      # 顺序 == DAILY_FAMILIES；bars 的目标以 'SPY' 开头、接着 13 只 ETF；sec_text 没有被调用
      # workers：bars / news / earnings 为 cfg.v3.collect_workers，sec_filings / fundamentals / sector 为 cfg.v3.sec_workers，universe / fred 为 1
      # sec_filings 的 whole_call_target == COMPANY_TICKERS_TARGET
      # 返回 8 行；data_fetch_runs 多了 8 行，run_id 都以 'v3-' 开头，job == 'v3'，trigger == 传入值

  def test_collect_daily_family_exception_becomes_whole_failure(v3_tx, monkeypatch):
      # news 的 run_adapter 抛 RuntimeError -> news 那一行 failures 含 '*'，其余族照常；Unavailable 则原样抛出

  def test_collect_extra_uses_explicit_targets(v3_tx, monkeypatch):
      # collect_extra(['AAPL', 'MSFT'], ...) -> 三个族各被调用一次，目标恰为这两只；返回 3 行
      # tickers == [] -> 不调用任何适配器，返回 []
  ```

  `tests/test_v3_job.py`（用 `v3_tx`，`collect=False`，日线直接写进 `price_bars`）：

  ```python
  def test_compute_is_deterministic_and_persisted(v3_tx, tmp_path):
      # 准备 SPY、QQQ、13 只 ETF 与 8 只股票各 300 根日线，其中 2 只在 session 当天放量突破
      r1 = run_facts(now=NOW, collect=False, cfg=cfg)
      r2 = run_facts(now=NOW, collect=False, cfg=cfg)
      assert r1['status'] == r2['status'] == 'ok' and r1['candidates'] == r2['candidates'] >= 2
      # 两次运行的 v3_candidates 在 (ticker, rank, reason) 上完全相同
      # v3_runs.draft_md 与 draft_path 指向的文件内容相同；文件名为 '<session>.draft.md'
      # latest_run(conn, session)['run_id'] == r2['run_id']

  def test_benchmark_missing_writes_error_run(v3_tx):
      # 不写 SPY 的日线 -> status 'error'，error 以 '基准日线缺失' 开头，candidates == 0
      # v3_runs 有一行 status 'error'；v3_candidates 没有这次运行的行；草稿含「今天没有草稿：」
      # QQQ 缺失（screen_rules.BenchmarkMissing）同样处理

  def test_holdings_and_coverage_reach_the_draft(v3_tx):
      # v3_trades 里有一笔买入、v3_plans 里有该股票的止损 -> 草稿「持仓」一节有这只股票
      # collect=False 时覆盖率一节写「本次没有采集，用的是库里已有的数据。」

  def test_cli_exit_codes(v3_tx, capsys):
      # main(['run', '--no-collect']) 正常返回 0 并打印 JSON；基准缺失返回 1
      # core_tx.connection 抛 Unavailable 时返回 2，stderr 一行「数据库不可用：…」，没有异常栈
      # main(['show']) 打印最新草稿；没有任何运行时返回 1 并打印「还没有运行记录。」
  ```

- [ ] **Step 7: 运行，确认失败**

  Run: 一次性库下 `tests/test_v3_collect.py tests/test_v3_job.py -q`
  Expected: FAIL（模块不存在）。

- [ ] **Step 8: 实现 `facts/collect.py`、`facts/job.py`、`facts/__main__.py`**

  - `target_facts`：关注列表经 `pool.from_watchlist` 取代码（`ValueError` 视为空）；持仓 = `facts_repo.list_trades` 按股票分组后 `positions.build(...)['position']` 非空者。
  - `collect_daily`：`deps` 缺省为 `adapter_deps_from_service(core_tx.Deps(), transaction=core_tx._tx, config=cfg, sleep=collect.FETCH_SLEEP)`。在 `collect.family_locks([...])`（`kind == 'fetched'` 且 `lock` 的族，按 `DAILY_FAMILIES` 顺序）内执行。先跑 `universe`，用 `observe` 拿到 `rows[0]['tickers']`；失败时退回 `universe.load().tickers`，再失败用空元组。其余各族：`fred` 的目标是 `adapter.targets(conn, 'daily')`，其他是 `adapter.targets(conn, 'daily', facts=facts, universe_loader=lambda: SimpleNamespace(tickers=loaded))`。每族一个 `Tally(adapter.id, 'v3', targets)`，`scope='daily'`。非 `Unavailable` 的异常记为 `tally.record('*', exc)`。最后 `collect.write_runs_with_ids(rows)`，返回 `rows`。
  - `collect_extra`：目标 `adapter.targets(conn, 'screen_new_members', explicit=tickers)`，`scope='screen_new_members'`；各族的 `workers` 与 `collect_daily` 相同（按 `YF_FAMILIES` / `SEC_FAMILIES` 取）；同样持族锁、同样写 `data_fetch_runs`。`trigger` 由调用方给，取值须在 `data_fetch_runs` 允许的集合内（`scheduled`、`manual_batch`、`manual_family`）。
  - `load_inputs`：股票范围 = `universe.load().tickers` ∪ 关注列表代码，去掉 SPY 与 `cfg.v3.sector_etfs`。日线统一 `read_bars(conn, t, session, cutoff_at=now, limit=300)`。宏观读 `data.market_constants.MACRO_SERIES`；没有 `FRED_API_KEY` 环境变量时 `macro_status` 各项为 `'not_configured'`，否则 `'ok'`。
  - `compute`：依次 `screen_rules.evaluate`（基准 QQQ）→ `market_view` → `setup_followthrough` → `industry_strength` → `rank_candidates`。SPY 在 `session` 没有日线时抛 `BenchmarkMissing('SPY: missing_signal_bar')`。
  - `run_facts`：`now` 缺省 `collect.wall_clock()`；`session = latest_closed_session(now)`；顺序为 `collect_daily` → `load_inputs` → `compute` → `collect_extra(前 top_n 只)` → 持仓（止损取 `facts_repo.latest_plan` 的 `stop`，最新收盘取日线末根 `close`）→ `coverage` → `render_draft` → 同一事务写 `v3_runs` 与 `v3_candidates` → 写文件。`BenchmarkMissing` 时写错误运行与错误草稿，错误文本 `基准日线缺失：<异常文本>`。`collect=False` 时跳过两次采集。
  - `__main__`：启动先 `load_dotenv()`。

- [ ] **Step 9: 运行，确认通过**

  Run: 一次性库下 `tests/test_v3_collect.py tests/test_v3_job.py -q`；离线 `tests/test_v3_import_boundary.py tests/test_layer_dependencies.py -q`
  Expected: 全部 PASS。

- [ ] **Step 10: Commit**

  ```bash
  git add investment_assistant/facts/collect.py investment_assistant/facts/job.py investment_assistant/facts/__main__.py tests/test_v3_collect.py tests/test_v3_job.py
  git commit -m "feat(v3): concurrent collection driver and the facts job with its CLI"
  ```

---

### Task 6: 工具面（MCP 服务）

**Files:**
- Create: `investment_assistant/toolsurface/{env,validation,reads,planning,chat,dispatch,jsonrpc,__main__}.py`
- Test: `tests/test_v3_tool_validation.py`、`tests/test_v3_tools_read.py`、`tests/test_v3_tools_plan.py`、`tests/test_v3_tools_chat.py`、`tests/test_v3_jsonrpc.py`

**Interfaces:**
- Consumes: Task 3 的 `facts.repo`、`V3Config`、`v3_tx`；Task 4 的 `holdings_view`、`coverage_lines`；Task 5 的 `facts.collect.collect_extra`（仅 `ensure_data` 用，Task 5 未完成时测试里替换它）。现有接口：`data.repo` 的读函数（签名见 Task 5）、`data.repo.read_news(conn, ticker, *, cutoff_at, since=None, limit=50, source=None)`、`read_filings(conn, ticker, *, since, cutoff_at)`、`financial_quarters(conn, ticker, *, cutoff_at, quarters=8)`；`derivation.indicators.price.indicators(bars, spy_bars) -> dict`、`price.atr14(bars) -> Decimal | None`；`execution.plan_levels.rule_levels(ref_price, atr)`、`levels_ordered(levels)`、`validity(signal_date) -> (date, date)`、`earnings_flags(row, *, signal_date, valid_from, valid_until) -> (invalid, warnings)`、`size(*, snapshot, ref_price, stop, entry_high, ticker_value, total_value, params) -> (sizing, invalid)`、`validate_levels(raw)`、`MAX_HOLD_RANGE`；`execution.entry_rules.ENTRY_TYPES`；`core.knowledge.catalog()`、`agent_text(cat, concept_id)`；`data.identity.stock_id(ticker)`；`core.tx._jsonable`。
- Produces（下一份 plan 的每日简报会话使用）:

  ```python
  # toolsurface/env.py
  @dataclass(frozen=True)
  class ToolEnv:
      session: date
      cutoff_at: datetime
      mode: str                 # 'brief' | 'chat'
      agent_session: str
      cfg: AssistantConfig
      read: Callable[[Callable[[Any], Any]], Any]     # 在只读事务里执行 fn(conn)
      write: Callable[[Callable[[Any], Any]], Any]    # 在读写事务里执行 fn(conn)
  def make_env(*, session: date, mode: str, agent_session: str, cfg: AssistantConfig | None = None,
               now: datetime | None = None) -> ToolEnv

  # toolsurface/validation.py
  TICKER_RE = re.compile(r'^[A-Z][A-Z0-9.\-]{0,9}$')
  def normalize_ticker(value: Any) -> str                                    # 不合法抛 ValueError('invalid_ticker')
  def in_scope(mode: str, ticker: str, top: list[str]) -> bool               # brief：ticker 在 top 里；chat：恒真
  def check_agent_levels(levels: dict[str, Decimal], *, last_close: Decimal, atr: Decimal) -> list[str]

  # toolsurface/dispatch.py
  @dataclass(frozen=True)
  class ToolDef:
      name: str
      fn: Callable[..., Any]
      modes: tuple[str, ...]
      description: str
      input_schema: dict
  def tool_defs(mode: str) -> list[ToolDef]
  def call(env: ToolEnv, name: str, args: dict) -> dict        # {'ref': 'call:N', 'data': ...} 或 {'ref': 'call:N', 'error': '<代码>[: 说明]'}

  # toolsurface/jsonrpc.py
  def handle(env: ToolEnv, message: dict) -> dict | None
  def serve(env: ToolEnv, stdin=sys.stdin, stdout=sys.stdout) -> None
  ```

  启动：`python -m investment_assistant.toolsurface --session YYYY-MM-DD --mode brief|chat --agent-session ID [--env-file PATH]`。数据库连接从 `--env-file` 指向的文件或进程环境里的 `INVESTMENT_ASSISTANT_DATABASE_URL` 取，配置文件里不出现凭据。

  工具清单（名称与参数逐字；除注明外两种模式都有）：

  | 工具 | 参数 | 返回的 `data` |
  |---|---|---|
  | `get_market` | 无 | 该交易日最新运行的 `market`：`session, label, regime_tier, breadth, spy_ret20, etf_table, macro` |
  | `get_candidates` | 无 | `{run_id, top_n, candidates: [{ticker, rank, reason, screen_group, trigger_rules, setup_tier, industry, industry_tier, rs, in_top_n, verdict}]}` |
  | `get_coverage` | 无 | `{families, missing_bars, lines}` |
  | `get_price_bars` | `ticker`，`lookback=60`（1–120） | `[{date, open, high, low, close, volume}]` |
  | `get_indicators` | `ticker` | `price.indicators` 的字典；没有日线时错误 `no_bars` |
  | `get_news` | `ticker`，`days=7`（1–14） | 最多 20 条 `{source, title, url, published_at}` |
  | `get_filings` | `ticker`，`days=30`（1–90） | 最多 20 条 `{form, filing_date, accepted_at, items, accession_number}` |
  | `get_earnings` | `ticker` | `{earnings_date, status}` 或 `null` |
  | `get_fundamentals` | `ticker` | `{rows: [...]}`，`financial_quarters` 的行，最多 40 行 |
  | `get_positions` | 无 | `holdings_view` 的行 |
  | `get_plans` | `session=None` | `{plans: [...], verdicts: {...}}` |
  | `get_concept` | `concept_id=None` | `knowledge.agent_text` 的结果 |
  | `compute_plan_levels` | `ticker` | `{ticker, session, ref_price, atr, levels, valid_from, valid_until, sizing, earnings: {invalid, warnings}, entry_types, max_hold_range}` |
  | `save_verdict` | `ticker`，`verdict`，`reasons`，`counter=''`，`refs=[]` | 保存后的行 |
  | `save_plan` | `ticker`，`thesis`，`invalidation`，`entry_type='zone_limit'`，`max_hold_sessions=10`，`levels=None`，`counter=''`，`refs=[]` | 保存后的行 |
  | `record_equity`（仅 chat） | `equity`，`request_id`，`confirmed`，`as_of=None`，`note=''` | `confirmed` 为假时 `{write: false, preview}`；为真时保存后的行 |
  | `record_trade`（仅 chat） | `ticker`，`side`，`quantity`，`price`，`executed_at`，`request_id`，`confirmed`，`fee=0`，`plan_id=None`，`note=''` | 同上 |
  | `ensure_data`（仅 chat） | `ticker` | `{families: {...}, lines: [...]}` |

  错误代码（`error` 字段以它开头）：`unknown_tool`、`not_available_in_mode`、`unknown_argument`、`missing_argument`、`invalid_ticker`、`invalid_argument`、`no_run`、`no_bars`、`atr_unavailable`、`out_of_scope`、`invalid_verdict`、`invalid_refs`、`no_confirm_verdict`、`invalid_entry_type`、`invalid_max_hold`、`shares_not_allowed`、`levels_invalid`、`earnings_in_validity`、`not_confirmed`、`position_invalid`、`unavailable`。

- [ ] **Step 1: 写失败的测试**

  `tests/test_v3_tool_validation.py`：

  ```python
  def test_normalize_ticker():
      assert normalize_ticker(' aapl ') == 'AAPL' and normalize_ticker('BRK.B') == 'BRK.B'
      for bad in ('', 'AA PL', '1ABC', 'TOOLONGTICKER', None, 123):
          with pytest.raises(ValueError, match='invalid_ticker'): normalize_ticker(bad)

  def test_in_scope():
      assert in_scope('brief', 'AAPL', ['AAPL']) and not in_scope('brief', 'MSFT', ['AAPL'])
      assert not in_scope('brief', 'AAPL', []) and in_scope('chat', 'MSFT', [])

  def test_check_agent_levels():
      # last_close 100、ATR 2。主体价位 entry_low 99 / entry_high 101 / stop 96 / target1 107 -> []
      # 顺序错（stop >= entry_low）-> ['unordered']
      # entry_high 102.01 -> 'entry_too_high'；entry_low 95.99 -> 'entry_too_low'
      # stop 距 entry_low 不足 1（0.5 ATR）-> 'stop_too_tight'；超过 6（3 ATR）-> 'stop_too_wide'
      # target1 - entry_high 小于 entry_high - stop -> 'reward_below_1r'
  ```

  `tests/test_v3_tools_read.py`（用 `v3_tx`，先写入一次运行、候选和日线）：

  ```python
  def test_read_tools_return_stored_facts(env):
      assert call(env, 'get_market', {})['data']['label'] == 'neutral'
      cands = call(env, 'get_candidates', {})['data']
      assert [c['ticker'] for c in cands['candidates'] if c['in_top_n']] == TOP5 and cands['top_n'] == 5
      assert len(call(env, 'get_price_bars', {'ticker': 'aapl', 'lookback': 3})['data']) == 3
      # get_news / get_filings 各自不超过 20 条；get_earnings 没有数据返回 None
      # 每次调用 ref 递增：'call:1'、'call:2'…；v3_tool_calls 行数与调用次数相同

  def test_cutoff_and_session_are_fixed_by_env(env):
      # 库里有 env.session 之后一天的日线 -> get_price_bars 的最后一根仍是 env.session
      # fetched_at 晚于 env.cutoff_at 的修订不被读到

  def test_bad_arguments_return_error_envelope(env):
      assert call(env, 'nope', {})['error'].startswith('unknown_tool')
      assert call(env, 'get_price_bars', {'ticker': 'AAPL', 'lookback': 100000})['error'].startswith('invalid_argument')
      assert call(env, 'get_price_bars', {'ticker': 'aa pl'})['error'].startswith('invalid_ticker')
      assert call(env, 'get_price_bars', {'ticker': 'AAPL', 'extra': 1})['error'].startswith('unknown_argument')
      assert call(env, 'get_price_bars', {})['error'].startswith('missing_argument')
      assert call(env, 'get_price_bars', {'ticker': 'ZZZZ'})['data'] == []
      assert call(env, 'get_indicators', {'ticker': 'ZZZZ'})['error'].startswith('no_bars')
      # 以上每次调用都记了一行，error 列有值；之后的正常调用照常成功

  def test_chat_only_tools_hidden_in_brief(env):
      assert 'record_trade' not in {t.name for t in tool_defs('brief')} and 'record_trade' in {t.name for t in tool_defs('chat')}
      assert call(env, 'record_equity', {'equity': 1, 'request_id': 'r', 'confirmed': True})['error'].startswith('not_available_in_mode')

  def test_no_run_for_session(v3_tx):
      # 该交易日没有运行记录 -> get_market / get_candidates 返回 'no_run'
  ```

  `tests/test_v3_tools_plan.py`（用 `v3_tx`；候选股票日线为 15 根以上、真实波幅恒为 2 的序列，最新收盘 100）：

  ```python
  def test_compute_plan_levels_uses_host_arithmetic(env):
      d = call(env, 'compute_plan_levels', {'ticker': TOP5[0]})['data']
      assert d['levels'] == {'entry_low': '99.00', 'entry_high': '101.00', 'stop': '96.00', 'target1': '108.00'}
      assert d['sizing']['status'] == 'no_equity' and d['sizing']['shares'] is None

  def test_save_plan_rejections(env):
      t = TOP5[0]
      assert call(env, 'save_plan', {'ticker': 'ZZZZ', **TEXTS})['error'].startswith('out_of_scope')          # 名单外标的
      assert call(env, 'save_plan', {'ticker': t, **TEXTS})['error'].startswith('no_confirm_verdict')        # 没有确认结论
      confirm(env, t)
      assert call(env, 'save_plan', {'ticker': t, **TEXTS, 'shares': 100})['error'].startswith('shares_not_allowed')
      assert call(env, 'save_plan', {'ticker': t, **TEXTS, 'entry_type': 'market'})['error'].startswith('invalid_entry_type')
      assert call(env, 'save_plan', {'ticker': t, **TEXTS, 'max_hold_sessions': 40})['error'].startswith('invalid_max_hold')
      bad = {'entry_low': 99, 'entry_high': 101, 'stop': 99.5, 'target1': 108}                              # 止损不在入场下方
      assert call(env, 'save_plan', {'ticker': t, **TEXTS, 'levels': bad})['error'].startswith('levels_invalid')
      # 以上每次之后 v3_plans 仍为空

  def test_save_plan_host_and_agent_levels(env):
      # 确认后不带 levels 保存 -> levels_source 'host'，价位等于 compute_plan_levels 的结果
      # 带合法 levels 保存 -> levels_source 'agent'，覆盖同一行（表里仍一行）
      # 财报日落在有效期内 -> 'earnings_in_validity'

  def test_plan_without_equity_and_without_room(env):
      # 没有本金记录 -> 保存成功，shares is None，warnings 含 '未提供账户资金，未算股数'
      # 记录本金 100000 后重存 -> shares == 247，sizing.status 'capped'，sizing.applied_cap 'single'
      #   （按风险：1% = 1000，每股风险 100 - 96 = 4，得 250 股；按单票上限 25%：25000 / 101 取整得 247 股；取较小者）
      # 另一只股票已有 1000 股、最新收盘 100（市值等于本金，占满总仓位上限）-> 保存成功，shares == 0，warnings 含 'no_room'

  def test_verdict_rules(env):
      # verdict 不是三者之一 -> 'invalid_verdict'；reasons 为空或超过 2000 字 -> 'invalid_argument'
      # refs 里有本会话不存在的 'call:99' -> 'invalid_refs'；confirm / veto 没有任何 ref -> 'invalid_refs'；unknown 可以没有 ref
      # 同一标的存两次 -> 一行，内容是第二次的

  def test_brief_scope_empty_top_n(v3_tx):
      # 运行里没有候选 -> brief 模式下 save_verdict / save_plan 对任何标的都返回 'out_of_scope'

  def test_chat_mode_allows_named_ticker(v3_tx):
      # chat 模式、名单外但有日线的股票 -> 可以保存结论与计划，origin == 'chat'
  ```

  `tests/test_v3_tools_chat.py`：

  ```python
  def test_record_equity_and_trade_need_confirmation(chat_env):
      r = call(chat_env, 'record_equity', {'equity': 50000, 'request_id': 'e1', 'confirmed': False})['data']
      assert r['write'] is False and '50000' in r['preview']            # 表里没有行
      call(chat_env, 'record_equity', {'equity': 50000, 'request_id': 'e1', 'confirmed': True})   # 写入一行；重放同 request_id 仍一行
      # record_trade：confirmed False 只返回复述；True 写入；卖出超过持仓 -> 'position_invalid' 且不写入
      # equity <= 0、quantity <= 0、side 不是 buy / sell -> 'invalid_argument'
      # plan_id 指向不存在的计划 -> 'invalid_argument'

  def test_ensure_data_calls_collect_extra(chat_env, monkeypatch):
      # 替换 facts.collect.collect_extra，断言被调用一次：tickers == ['NFLX']，families == ('bars', 'news', 'sec_filings', 'earnings', 'fundamentals')
      # 非美股代码 -> 'invalid_ticker'，collect_extra 不被调用
  ```

  `tests/test_v3_jsonrpc.py`（不需要数据库，`dispatch.call` 用假的）：

  ```python
  def test_initialize_list_and_call(env):
      r = handle(env, {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18'}})
      assert r['result']['protocolVersion'] == '2025-06-18' and r['result']['capabilities'] == {'tools': {}}
      assert handle(env, {'jsonrpc': '2.0', 'method': 'notifications/initialized'}) is None
      tools = handle(env, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})['result']['tools']
      assert {'name', 'description', 'inputSchema'} <= set(tools[0]) and all(t['inputSchema']['type'] == 'object' for t in tools)
      out = handle(env, {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'get_market', 'arguments': {}}})['result']
      assert json.loads(out['content'][0]['text'])['ref'] == 'call:1' and out['isError'] is False
      # 工具返回错误信封时 isError is True
      # 客户端请求的 protocolVersion 不认识 -> 回 '2025-06-18'

  def test_unknown_method_and_ping(env):
      assert handle(env, {'jsonrpc': '2.0', 'id': 4, 'method': 'resources/list'})['error']['code'] == -32601
      assert handle(env, {'jsonrpc': '2.0', 'id': 5, 'method': 'ping'})['result'] == {}

  def test_bad_lines_do_not_stop_the_loop(env):
      # stdin 依次给：一行非 JSON、一行 JSON 数组、一条合法的 tools/list
      # stdout 得到：id 为 null 的 -32700、id 为 null 的 -32600、tools/list 的正常回复；每条一行、以换行结尾
      # tools/call 的 arguments 不是对象 -> -32602
  ```

- [ ] **Step 2: 运行，确认失败**

  Run: `<离线前缀> tests/test_v3_tool_validation.py tests/test_v3_jsonrpc.py -q`；一次性库下 `tests/test_v3_tools_read.py tests/test_v3_tools_plan.py tests/test_v3_tools_chat.py -q`
  Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现**

  测试没有钉死、需要照此实现的规则：

  - `make_env`：`cutoff_at` 取 `now`（缺省当前时刻）；`read` 用 `core_tx.read_only_tx()`，`write` 用 `core_tx._tx()`。所有读工具传 `end_date=env.session`、`cutoff_at=env.cutoff_at`，工具参数里没有日期。
  - `dispatch.call`：按 `ToolDef.input_schema` 检查多余与缺失的参数；`save_plan` 的参数里出现 `shares` 或 `quantity` 时返回 `shares_not_allowed` 而不是 `unknown_argument`。工具函数抛 `ValueError('<代码>[: 说明]')` 即为错误信封；其他异常记为 `invalid_argument: <异常文本前 200 字>`；`core_tx.Unavailable` 记为 `unavailable`。每次调用（含出错的）写一行 `v3_tool_calls`：`seq = facts_repo.next_seq`，`result_sha256` 为 `json.dumps(data, sort_keys=True, ensure_ascii=False)` 的 SHA-256，出错时为空。写记录失败不影响返回。
  - `check_agent_levels`：先 `plan_levels.levels_ordered`，不过则只返回 `['unordered']`。其余四条：`entry_high <= last_close + 1 × ATR`；`entry_low >= last_close − 2 × ATR`；`0.5 × ATR <= entry_low − stop <= 3 × ATR`；`target1 − entry_high >= entry_high − stop`。
  - `compute_plan_levels`：`ref_price` 取 `session` 当天未复权 `close`；`atr = price.atr14(bars)`，为空或不大于 0 返回 `atr_unavailable`；价位 `rule_levels(ref_price, atr)`；有效期 `validity(env.session)`；本金快照 `{'id', 'as_of', 'equity'}` 取 `facts_repo.latest_equity`，没有则 `None`；`ticker_value` 与 `total_value` 由 `holdings_view` 的数量乘最新收盘得出；`params=env.cfg.workbench`。金额与价位在返回里一律转成字符串。
  - `save_plan` 的检查顺序：范围 → 确认结论 → 入场类型 → 最长持有 → 价位 → 财报日。`levels` 给了就先 `plan_levels.validate_levels` 再 `check_agent_levels`，不过返回 `levels_invalid: <原因用逗号连接>`。仓位状态 `no_equity` 时 `shares` 为空并加提示「未提供账户资金，未算股数」；`no_room` 时 `shares` 为 0 并加提示 `no_room`。`earnings_flags` 返回的 `warnings` 并入计划的 `warnings`。
  - `save_verdict`：`reasons` 1–2000 字；`refs` 必须都在 `facts_repo.tool_call_refs(conn, env.agent_session)` 里。
  - `record_trade`：`confirmed` 为真时先把这笔成交并入现有成交试算 `positions.build`，抛 `PositionError` 则返回 `position_invalid` 且不写入。`preview` 是一句中文复述，含代码、方向、数量、价格、时间。
  - `ensure_data`：代码先过 `normalize_ticker` 再过 `data.identity.stock_id`；调用 `collect_extra([ticker], session=env.session, now=当前时刻, run_id=facts.collect.new_run_id(now), trigger='manual_family', cfg=env.cfg, families=('bars', 'news', 'sec_filings', 'earnings', 'fundamentals'))`，返回值经 `coverage` 与 `coverage_lines` 整理。
  - `jsonrpc`：换行分隔，每条回复一行并立即 `flush`。`initialize` 的 `serverInfo` 为 `{'name': 'ia', 'version': '3.0'}`。`tools/call` 的结果文本是信封的 JSON（`ensure_ascii=False`）。`serve` 读到 EOF 正常返回。
  - 每个工具的 `description` 用一句中文写清它返回什么、什么时候用；`input_schema` 是 JSON Schema（`type: object`、`properties`、`required`、`additionalProperties: false`）。

- [ ] **Step 4: 运行，确认通过**

  Run: 同 Step 2，另加 `<离线前缀> tests/test_v3_import_boundary.py tests/test_layer_dependencies.py tests/test_core_tx_single_definition.py -q`
  Expected: 全部 PASS。

- [ ] **Step 5: 手工冒烟——用 Task 2 的方式让真实客户端连一次（Task 2 已通过时做）**

  对一次性库写入一次运行后，把 `scripts/pi_precheck/run_checks.py` 的 `mcp_roundtrip` 里的服务换成 `python -m investment_assistant.toolsurface --session <该交易日> --mode brief --agent-session smoke --env-file <指向一次性库的文件>`，提示词改为「调用 get_candidates，只回复排名第一的股票代码」。
  Expected: 最终回复是排名第一的代码；`v3_tool_calls` 里有 `agent_session = 'smoke'` 的行。与 `docs/operations/pi-unattended.md` 记录的线上格式有出入时，以实测为准修正 `jsonrpc.py` 并补测试。Task 2 尚未通过时，本步留到 Task 7。

- [ ] **Step 6: Commit**

  ```bash
  git add investment_assistant/toolsurface tests/test_v3_tool_validation.py tests/test_v3_tools_read.py tests/test_v3_tools_plan.py tests/test_v3_tools_chat.py tests/test_v3_jsonrpc.py
  git commit -m "feat(v3): MCP tool surface with host-validated verdicts and plans"
  ```

---

### Task 7: 收尾——全量测试、真实环境试跑、评审与文档

**Files:**
- Create: `docs/operations/v3-facts.md`
- Modify: `docs/roadmap.md`、`docs/README.md`、`CHANGELOG.md`

**Interfaces:**
- Consumes: Task 1–6 的全部产物。
- Produces: 下一份 plan（每日简报）的起点——一份真实的草稿、实测的事实任务耗时、迁移 032 已在真实库执行。

- [ ] **Step 1: 离线全量测试**

  Run: `<离线前缀> tests/ -q`
  Expected: 全部通过（库测试跳过）。

- [ ] **Step 2: 一次性库全量测试**

  按 memory「Disposable Postgres for SQL tests」起一次性库（端口 55433），设 `INVESTMENT_ASSISTANT_TEST_DATABASE_URL` 后 Run: `.venv/bin/python -m pytest tests/ -q`
  Expected: 全部通过，0 跳过。

- [ ] **Step 3: 全量 code review（一轮）**

  派 `opus` 审查从本 plan 第一个提交到 HEAD 的全部改动，重点是 Review Focus 五条、import 边界、`run_adapter` 默认路径是否与改动前等价、SQL 是否只触碰 `v3_` 表与事实表。修复后由编排者看 diff 与相关测试确认。

- [ ] **Step 4: 请用户授权真实库迁移与一次真实试跑（先停下来问）**

  向用户说明将要做的三件事并等待明确同意：`pg_dump` 备份真实库；对真实库执行迁移 032（只新建七张 `v3_` 表）；用真实网络跑一次 `python -m investment_assistant.facts run`（会向共用的事实表和 `data_fetch_runs` 写入，时间避开旧服务 11:30–12:10 的运行窗口）。用户不同意则跳到 Step 7，并在文档里写明这三项未做。

- [ ] **Step 5: 备份、迁移、试跑并计时**

  ```bash
  pg_dump "$INVESTMENT_ASSISTANT_DATABASE_URL" -Fc -f .local/v3/backup-before-032.dump
  .venv/bin/python -m investment_assistant.migrate
  time .venv/bin/python -m investment_assistant.facts run
  .venv/bin/python -m investment_assistant.facts show
  ```

  Expected: `migrate` 输出 `{"applied": ["032_v3_agent_first.sql"], "count": 1}`；`run` 退出码 0；`show` 打印出含五个小节的草稿，名单每行有规则理由。记下总耗时和各族耗时。**不到 5 分钟的目标没达到时**：把 `collect_workers` / `sec_workers` 的实测影响记下来，如实写进文档，不为了达标去改口径或跳过族。

- [ ] **Step 6: Task 6 Step 5 的手工冒烟（若当时未做）**

  对真实库的这次运行做一遍，Expected 同 Task 6 Step 5。

- [ ] **Step 7: 写文档**

  - `docs/operations/v3-facts.md`：事实任务的两条命令、退出码、草稿文件位置、`"v3"` 配置键与默认值、并行期与旧系统共用事实表的说明、Step 5 的实测耗时（或「未实测」）、迁移 032 的执行记录与备份文件位置。
  - `docs/roadmap.md`：第 0 步、第 1 步的状态按实际结果更新；写明概念卡归档推迟到第 5 步。
  - `docs/README.md`：在 v3 一节加上两份运维文档的链接。
  - `CHANGELOG.md`：顶部加「v3（进行中）」一节，列出本 plan 交付的内容与三处与 spec 的出入。

- [ ] **Step 8: 跑文档测试并提交**

  Run: `<离线前缀> tests/test_docs_links.py tests/test_no_zhipu_residue.py -q`
  Expected: PASS。

  ```bash
  git add docs CHANGELOG.md
  git commit -m "docs(v3): facts job operations, roadmap status and changelog"
  ```
