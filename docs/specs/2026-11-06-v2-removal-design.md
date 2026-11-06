# 提前移除 v2（旧系统下线）设计

- 日期：2026-11-06
- 范围：把 [v3 设计](2026-11-05-agent-first-rebuild-design.md) §10 / §15.6 的第 5 步「旧系统下线」提前到第 1 步之后做。
- 与 v3 设计的关系：删什么、留什么仍以 v3 设计 §9 与 §15.5 为准；本文只改顺序，并把删除范围落到可执行的规则上。冲突之处以本文为准，逐条列在 §6。

---

## 1. 为什么现在做

用户原话（2026-11-06）：「v3 第一个版本已经完成，接下来我想要移除 v2 版本，以 v3 为主要迭代方向。」随后确认：能接受删除后到第 2 步完成前没有每日推送和页面；是否现在删交给实施者判断，「如果删除后更方便迭代就可以 subagent 删除」。

判断是现在删（我的判断，依据是 2026-11-06 对 `main`（`d082400`）量出来的数）：

| 项 | 数 |
|---|---|
| 后端模块 | 256 个、48,601 行 |
| v3 两个入口（`facts`、`toolsurface`）实际 import 到的模块（含函数体内的 import） | 89 个、11,158 行 |
| 不在其中的 | 167 个、37,443 行（77%） |
| 测试文件 | 234 个，其中只碰保留模块的 63 个，其余约 170 个碰到旧代码 |
| 前端 | `lib/components` 107 个文件、12 个分区页面全部属于旧系统；`lib/ui` 组件层 146 个文件保留 |

不删的代价落在第 2、3 步：新页面要挤进旧路由、定时要与旧任务错开（12:30）、新代码要靠 import 边界测试防着旧代码、129 张概念卡不能动、三套全量测试大部分时间在测 v2。删除的工作量早做晚做一样；v2 完整保留在 git 历史里（最后一个含 v2 的提交是 `d082400`）。

## 2. 目标、非目标、反目标

**目标**

1. 仓库里只剩 v3 用到的代码、保留的外壳和它们的测试；三套全量测试（离线 pytest、一次性库 pytest、前端 `npm test && npm run build`）通过。
2. `python -m investment_assistant.facts run|show` 与 `python -m investment_assistant.toolsurface` 的行为不变。
3. 外壳可启动：`start.command` 启动的服务能托管前端构建产物，首页是一个占位页，`#_ui` 组件画廊可用。
4. 文档只描述现在的系统；讲 v2 的文档和概念卡都在 `docs/archive/v2/`。
5. 删旧表的迁移写好，并在一次性库上验证。

**非目标**

- 不做 v3 第 2、3、4 步的任何功能（简报、运行时接口、对话、新页面、新的 Discord 模板、v3 定时）。
- 不重组保留模块的目录，不改名，不改保留函数的行为。
- 不打 tag、不 push、不改版本号（CHANGELOG 仍记在「v3（进行中）」下）。

**反目标**

- `facts` 或 `toolsurface` 的输出因为这次删除而变化。
- 在真实库上执行删表迁移而没有用户的单独授权。
- 留下「兼容旧系统」的空壳、重新导出或注释掉的代码。

## 3. 约束

- 主干开发，直接在 `main` 上提交（项目约定）。
- 真实库只读不写：本次不在真实库上执行任何迁移。删表迁移 `033` 的执行需要用户单独授权，执行前先 `pg_dump`（v3 设计 §9）。
- 旧服务 `local.investment-assistant` 设了 `KeepAlive`，删代码前必须先停（`launchctl bootout`），否则它会在删了一半的代码上继续跑定时任务。plist 文件不动。
- 冻结的 spec / plan 只修失效链接，不改正文（`docs/README.md` 的维护规则）。
- 数据库测试只连一次性库（`INVESTMENT_ASSISTANT_TEST_DATABASE_URL`），不连真实库。

## 4. 做法比较

| 做法 | 内容 | 取舍 |
|---|---|---|
| **A. 现在删，分三段**（选定） | 先按 import 闭包删掉整块旧代码与旧前端；再裁剪保留模块里没人调用的部分、归档概念卡、写删表迁移；最后重写文档并跑全量 | 一次到位，第 2、3 步在干净的代码上做。空窗期没有推送和页面（用户已接受）。删表迁移写好但不在真实库执行，旧表多留一段时间，不影响迭代 |
| B. 按原 spec 等第 2 步稳定 3 个交易日再删 | 顺序不变 | 没有空窗，但第 2、3 步全程背着 77% 的旧代码；用户已明确要以 v3 为主 |
| C. 现在只删整块，不裁剪保留模块、不写删表迁移 | 只做 A 的第一段 | 最快，但保留模块里还留着读写旧表的函数，旧表无法判定能不能删，第 5 步还要再来一次 |

选 A。B 与用户的方向相反；C 留下的尾巴正好是最容易被遗忘的部分。

## 5. 设计

### 5.1 保留集

保留的后端代码 = 以下三部分的并集，其余全部删除：

1. **v3 闭包**：从 `investment_assistant/facts/` 与 `investment_assistant/toolsurface/` 出发，沿 import（含函数体内的 import）能到达的全部模块。
2. **`data/` 与 `derivation/` 整层**（v3 设计 §9）。闭包外的 `data/acquisition.py`、`data/families.py`、`data/sec/{monitor,report,storage}.py`、`derivation/chain.py`、`derivation/indicators/{fiscal,__main__}.py` 只依赖保留模块，原样保留。
3. **外壳**（v3 设计 §15.5）：
   - `api/` 只留 `app.py`、`server.py`、`auth.py`、`http.py`、`static_files.py`。`app.py` 去掉调度线程、SSE 事件流和全部旧路由，保留认证中间件、异常处理和静态托管；`routes/` 与 `status_page.py` 删除。
   - `notify/` 只留 `discord.py`（发送通道）与 `message.py`；`discord.py` 末尾依赖旧模板的命令行演示删除。
   - `migrate.py`、`db.py`、`config.py`、`runtime_paths.py`。

整包删除：`workflow/`、`views/`、`services/`、`tasks/`、`review/`、`optimize/`、`analysis/agent/`、`core/llm/`、`execution/paper/`、`api/routes/`，以及 `analysis/`、`execution/`、`core/`、`notify/` 里不在保留集的模块；五个空目录 `hermes/`、`market/`、`research/`、`strategies/`、`tickers/`。

### 5.2 保留模块的裁剪

整块删完之后，保留模块里会剩下只服务旧系统的函数、常量和配置项。规则：

- 一个函数、类或常量，在保留的包代码与 `scripts/` 里已经没有任何引用（测试不算引用），就删除，连同只测它的测试。
- 不改保留下来的函数的签名与行为；不为了「更干净」移动代码。
- 重点模块：`db.py`、`data/repo.py`、`core/` 各模块（尤其 `knowledge.py`、`stages.py`、`schema.py`、`versions.py`、`texts.py`、`features.py`）、`config.py`、`execution/` 与 `analysis/` 的保留模块。
- `config.py` 里没有读者的配置类与配置键删除，`config/investment-assistant.example.json` 同步；`config/roadmap/` 删除。
- `requirements.txt` 去掉 `langgraph`、`langgraph-checkpoint-postgres`、`langchain-openai`，以及其他已无 import 的依赖。
- `tests/test_v3_import_boundary.py` 保留；允许名单按裁剪后的实际情况更新。

### 5.3 测试

- 只 import 被删模块的测试：删除。
- 只 import 保留模块的测试：保留，必须照常通过。
- 两边都碰的测试：保留其中只涉及保留模块的用例，其余删除；一个文件删到没有用例就删文件。
- 读前端源码的 Python 测试（`test_*_frontend_sync.py`、`test_dashboard_frontend_source.py`、`test_frontend_design_guards.py`）：只留仍然成立的静态守护（设计令牌、组件层规则），断言旧分区页面的部分删除。
- `tests/conftest.py` 的库夹具改为应用到迁移 033 为止。保留的数据库测试都在删完旧表的 schema 上跑，以此证明保留代码不依赖任何被删的表。
- 测旧迁移（001–031）的测试：不 import 被删代码且照常通过的保留，其余删除。

### 5.4 前端

- 保留：Svelte 工程与构建配置、`lib/ui` 组件层、`styles/`（令牌）、`lib/theme.ts`、`lib/utils.ts`、`routes/UiGallery.svelte`，以及不依赖业务类型的通用模块（判断标准：删掉业务模块后仍能独立编译，且第 3 步用得上——路由、请求封装、SSE、格式化）。
- 删除：`lib/components` 全部、12 个分区页面与它们的测试、`lib/charts`、所有业务模块（`workbench`、`accounts`、`growth`、`runs`、`timeline`、`derivation`、`knowledge`、`research`、`roadmap` 等）及夹具。
- `lib/ui` 里依赖业务模块的组件：只为类型而依赖的，把用到的类型挪进组件层；本身就是业务组件的，删除。`design-system.md` 的组件清单随之更新。
- `app.svelte` 改成外壳：首页是占位页，写明「今日简报与对话在 v3 第 3 步实现」；`#_ui` 仍进入组件画廊。
- 没有 import 的 npm 依赖（预计 `echarts`、`lightweight-charts`）从 `package.json` 去掉。

### 5.5 概念卡

按 v3 设计 §8 的划分：`indicator`（15）、`rule`（10）、`plan`（15）三组共 40 张保留在 `docs/knowledge/concepts/`；`system`（25）、`review`（24）、`agent`（23）、`family`（9）、`account`（8）五组共 89 张与 `docs/knowledge/principles.md` 移入 `docs/archive/v2/knowledge/`。

保留的 40 张：`related` 里指向已归档卡的条目删除；`sources` 里指向已删文件的条目删除；正文里指向已归档卡的链接改为指向归档位置。正文里描述旧系统行为的句子不在本次改写（留给第 4 步「概念学习」）。`core/knowledge.py` 按 5.2 的规则裁剪，`toolsurface` 读概念卡的两个调用（`catalog`、`agent_text`）行为不变，只是卡变少。

### 5.6 旧表：迁移 033

- 删除的表 = 迁移 001–032 建出来、现在还存在的表里，裁剪后的保留代码不再引用的那些；v3 设计 §9 点名保留的事实表（日线、资讯、申报、财报日、宏观、行业归类、基本面观察、拉取记录、关注列表）与七张 `v3_` 表不删。v3 设计 §9 点名要删的 `accounts`、`trades`、`account_snapshots`、`cash_flows` 必须在删除清单里。
- LangGraph 的检查点表不是迁移建的，用 `DROP TABLE IF EXISTS` 一并删除。
- 迁移先处理指向被删表的外键（迁移 027、029、030 加的），再删表；整个迁移在一个事务里。
- 有一个静态测试：迁移 033 删除的每张表，在保留的包代码里都搜不到引用。
- **不在真实库执行。** 执行步骤（`pg_dump` → 执行 → 核对）写进 `docs/operations/migrations-and-backup.md`，等用户授权。在那之前真实库比代码多出一些旧表，保留代码不读不写它们。

### 5.7 服务

- 执行开始时 `launchctl bootout gui/$(id -u)/local.investment-assistant` 停掉旧服务。
- 删完之后 `start.command` 启动的是外壳。本次不重新加载服务（我的判断：外壳在第 3 步之前没有内容可看）；plist 留在原处，下次登录时 launchd 会加载它并启动外壳，这是无害的。重新加载与彻底卸载的命令都写进 `docs/operations/local-service.md`。

### 5.8 文档

| 文档 | 处理 |
|---|---|
| `docs/roadmap.md` | 第 5 步标为提前完成，注明真实库删表待授权；写明顺序变化的原因，指向本文 |
| `docs/getting-started.md` | 按 v3 重写：安装、配置、建库、`facts` 与 `toolsurface` 命令、三套测试 |
| `docs/operations/local-service.md` | 改写为外壳服务的说明 |
| `docs/operations/scheduling-and-notifications.md` | 移入 `docs/archive/v2/operations/` |
| `docs/operations/migrations-and-backup.md` | 加上 033 与执行步骤 |
| `docs/operations/v3-facts.md` | 「并行期」一节改为并行期已结束；其余实测记录不动 |
| `docs/design-system.md` | 组件清单与画廊说明按 5.4 更新 |
| `docs/README.md`、`README.md`、`scripts/README.md`、`CLAUDE.md` | 去掉对旧系统的现在时描述 |
| `CHANGELOG.md` | 在「v3（进行中）」下加一段：移除 v2、与 v3 设计的出入 |
| `scripts/` | 只为 v2 服务的一次性脚本删除（`siwc_precheck.py`、`archive_v2_docs.py`、`make_numbers_snapshot.py`、`verify_cross_market.py`、`spike_backup_sources.py`、`rewrite_concept_sources.py`）及其测试；`start_local.py` 与 `pi_precheck/` 保留 |

## 6. 与 v3 设计的出入

| v3 设计 | 原来 | 现在 |
|---|---|---|
| §10 切换条件 | 第 2 步连续 3 个定时交易日产出简报后卸载旧服务 | 第 1 步完成后即下线（用户决定接受空窗） |
| §10 / §15.6 顺序 | 0 → 1 → 2 → 3 → 4 → 5 | 0 → 1 → 5 → 2 → 3 → 4 |
| §10 新旧并行期 | 共用事实表，定时错开到 12:30 | 没有并行期；第 2 步的定时直接用 11:30 |
| §15.5 并行期新页面加进现有服务、旧分区原样保留 | 并行 | 第 3 步直接在外壳上加页面 |
| §10 版本 | 切换完成时记为 `v3.0.0` | 本次不动版本号；`v3.0.0` 留到第 2、3 步交付之后由用户决定 |
| §9 删旧表 | 切换时执行 | 迁移写好并验证，执行另行授权 |

## 7. 验收

| # | 标准 |
|---|---|
| 1 | 三套全量测试通过：离线 pytest、一次性库 pytest（夹具应用到 033）、前端 `npm test && npm run build` |
| 2 | `python -m investment_assistant.facts show` 在一次性库上的输出与删除前一致（同一份入库数据重算，名单与排序不变，由现有 v3 测试保证） |
| 3 | 在 `investment_assistant/` 里搜不到对已删模块的 import；`python -c "import investment_assistant.api.server"` 成功 |
| 4 | 外壳启动后 `/` 返回占位页，`#_ui` 画廊可用，未认证的请求按原规则处理 |
| 5 | 迁移 033 在一次性库上执行成功；它删除的每张表在保留代码里没有引用；事实表与 `v3_` 表都还在 |
| 6 | `docs/knowledge/concepts/` 剩 40 张卡，`toolsurface` 的概念卡工具测试通过；`tests/test_docs_links.py` 通过 |
| 7 | `requirements.txt` 里没有 LangGraph / LangChain；`web/package.json` 里没有无人 import 的依赖 |
| 8 | 真实库没有被写入；旧服务已停 |

## 8. 留给实施者

- 5.2 裁剪的具体清单（按规则逐模块核对）；`core/features.py` 等「保留代码只用到一小部分」的模块裁到什么程度。
- 迁移 033 的删除清单与外键处理顺序。
- 前端通用模块逐个去留；占位页的具体文案与布局（用组件层现有组件）。
- 混合测试文件里用例的逐个去留。
- 文档的具体措辞。
