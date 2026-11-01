# 知识层：概念卡与原理页

v2.6 新增（知识层 `knowledge-1`）：把「系统里每个概念是什么意思、系统每天怎么跑」写成随代码版本走的概念卡与一页原理说明，由后端加载、校验、渲染，再经只读接口、命令行和 Agent 工具交给界面与 Agent。设计见 [v2.6 看懂系统 spec](../specs/2026-10-28-v2.6-understanding-the-system-design.md)（§4、§5.2、§10、§11）。本篇先写知识层本身，再写页面，最后是 v2.7 的「我的知识」。

## 做什么

- **一卡一概念**：`docs/knowledge/concepts/<id>.md`，开头 YAML frontmatter，后面 Markdown 正文。分八组：数据族、指标、规则 v1、规则计划 `plan-1`、Agent 计划、账户与模拟交易、复盘、系统运作。
- **一页原理**：`docs/knowledge/principles.md`，按 `## ` 二级标题分节，讲系统每天怎么跑。
- **随代码走**：卡与代码版本同在仓库里；定义或口径变了，卡的 `version` 加 1（改措辞、补例子不加）。系统不保存旧版正文，旧版看 git 历史。
- **源文件只来自仓库**：渲染时关闭原始 HTML，卡内只允许指向其它卡的链接，不会把人引回仓库其它文档。
- **不碰数据库**：加载只读文件，数据库不可用时知识接口照常工作。

## 卡的格式与校验

frontmatter 字段只有下表这些，多余字段校验失败：

| 字段 | 必填 | 规则 |
| --- | --- | --- |
| `id` | 是 | `^(family\|indicator\|rule\|plan\|agent\|account\|review\|system)\.[a-z0-9_]+$`；必须等于文件名去掉 `.md` |
| `title` | 是 | 中文名，1–30 字 |
| `group` | 是 | 八组之一，且等于 id 的前缀 |
| `version` | 是 | 正整数 |
| `applies_to` | 是 | 代码版本号列表，可为空；每项必须在 `knowledge.KNOWN_VERSIONS` 里 |
| `summary` | 是 | 一句话摘要，1–120 字，不含换行（就地展开显示它） |
| `related` | 是 | 互链的概念 id 列表，可为空；每项必须存在，不能是自己 |
| `sources` | 否 | 仓库内路径列表，每项必须存在；只给维护者看「改这些文件时回看这张卡」 |

正文 1–6000 字，推荐四个二级标题（按需省略）：「是什么」「怎么算 / 从哪来」「在系统里哪里看到」「注意」。

**链接**：卡内只许行内写法 `[文字](<id>.md)` 指向其它卡（目标必须存在）；引用式（`[文字][r]`）、带标题（`"…"`）和文字里含方括号的写法校验失败，这样给 Agent 的纯文本改写与反链才与渲染结果一致。外链、图片、`../` 路径、锚点一律校验失败。原理页另许 `concepts/<id>.md` 与 `#stage-<阶段 id>`；原理页的 `## ` 标题里不能有链接，接口里的 `heading` 是纯文本（行内代码不带反引号）；每个阶段在原理页里最多被链一次，一节里最多链一个阶段，阶段 id 必须是注册表 `stages.STAGE_IDS` 里的。

`KNOWN_VERSIONS` 是字面集合（当前与仍留在旧行里的历史版本号，共 58 个，含 v2.11 账户与模拟交易口径）。它不 import 各模块的常量，因为 Agent 工具要 import 本模块；`tests/test_knowledge.py` 逐一对齐代码常量，代码发了新版本号而这里没加，测试失败。

校验**一次收集全部问题**再报（缺字段、多余字段、链接问题各自成一条，不会互相挡住；`related` 的存在性按 `concepts/` 下全部文件名判断，一张卡 YAML 写坏不会让别的卡误报）；`sources` 必须是仓库内的相对路径；`title` / `summary` 先去首尾空白再判长度。每条形如 `concepts/plan.stop.md: related 里的 indicator.atr 不存在`。目录不存在或为空是空目录，不是错误；原理页缺失则 `principles` 为空。

## 引用形式

`concept:<id>@<version>`（如 `concept:plan.stop@1`）；`concept:<id>` 或裸 `<id>` 表示当前版。`knowledge.parse_ref` 解析，格式不对抛 `ValueError`。请求的版本不是当前版时返回当前版，并带 `stale`：「该引用指向第 n 版，当前为第 m 版；概念卡不保存旧版正文」。

## 加载与缓存（改卡需重启）

- 首次使用时整目录加载、校验、渲染，结果在进程内缓存到重启；服务启动时不加载，所以坏卡不会拖垮启动，批处理与其它页面不受影响。
- 加载失败**不缓存**，下一次调用重试。改了卡后需重启服务才生效（测试里用 `knowledge.reset_cache()` 或 `catalog.cache_clear()`）。
- 渲染：`markdown-it-py`（CommonMark + 表格，原始 HTML 关闭，正文里的 `<script>` 只会被转义显示）。链接改写在渲染前的 token 层做：卡链接变成 `#knowledge/<id>` 并带 `data-concept`，原理页的阶段链接变成 `#today` 并带 `data-stage`。frontmatter 用 `yaml.safe_load`。
- 给 Agent 的纯文本：正文源文本里的卡链接改写为 `文字（concept:<id>）`，其余原样。`content_sha256` 是正文源文本的 SHA-256。
- `Catalog.backlinks[id]`：哪些卡的 `related` 或正文链到它（按 id 排序）。

## 接口

三个只读 GET，放在 `api/routes/knowledge.py`，不依赖数据库：

| 接口 | 返回 | 错误 |
| --- | --- | --- |
| `/api/knowledge/concepts` | `{knowledge_version, groups: [{id, title, count}], concepts: [{id, ref, title, group, version, applies_to, summary, related}]}`，按分组顺序再按 id | 加载失败 503 |
| `/api/knowledge/concepts/{ref}` | 一张卡：上面的字段 + `html`、`related_cards`、`backlinks`（均为 `[{id, title}]`）、`stale`（当前版时为 `null`） | 格式错 400；不存在 404；加载失败 503 |
| `/api/knowledge/principles` | `{title, version, sections: [{heading, html, stage, concepts}]}` | 原理页缺失 404；加载失败 503 |

加载失败时响应为 `{"error": "概念卡加载失败：<第一条问题>", "problems": [...]}`。规则的中文名表在 `workbench/labels.py`（`RULE_TEXT`、`RULE_SHORT`、`ACTION_TEXT`），不依赖其它工作台模块。

## CLI

```bash
python -m investment_assistant.workbench.knowledge check [--root PATH]   # 校验整个目录
python -m investment_assistant.workbench.knowledge show <ref> [--root PATH]   # 打印一张卡
```

`check` 通过时打印「概念卡 n 张、原理页 m 节，通过」，退出码 0；有问题逐条打印，退出码 1。`show` 打印 frontmatter 字段和给 Agent 的纯文本；id 不存在或格式不对退出码 1。

## 怎么验证一次

1. 跑 `python -m investment_assistant.workbench.knowledge check`，应通过。
2. 离线测试：`env -u INVESTMENT_ASSISTANT_DATABASE_URL -u INVESTMENT_ASSISTANT_TEST_DATABASE_URL -u TEST_DATABASE_URL .venv/bin/python -m pytest tests/test_knowledge.py tests/test_workbench_labels.py tests/test_api_knowledge.py tests/test_api_contract.py -q`。
3. 服务启动后 `curl -s localhost:8787/api/knowledge/concepts`，看分组与计数；`curl -s localhost:8787/api/knowledge/concepts/plan.stop@9` 看 `stale`。
4. 故意把某张卡的 `version` 改成 0 再 `check`，应报出该文件名与原因；改回。

## 已知限制

- 卡与原理页的**内容**由后续任务写入；本篇描述的是加载、校验与渲染。目录里没有卡时接口返回空列表。
- 改卡后必须重启服务；没有热加载。
- 概念卡不保存旧版正文：`@n` 引用指向旧版时只返回当前版加 `stale` 说明。Agent 实际读到的正文连同 SHA-256 存在 `agent_tool_calls`，可回看。
- 校验只管格式与引用；卡里的数字与代码常量是否一致，由读真实目录的守护测试负责。
- 给 Agent 的纯文本用对源文本的替换生成：围栏代码块与行内代码里的卡链接写法保持原样（不改写、不计入反链）；卡链接写在缩进式代码块（四空格缩进）里时，改写数与渲染出的链接数对不上，该卡校验失败（提示里写明「或写在缩进代码块里」），所以卡正文里不要这样写。反链来自校验过的正文链接（token），不来自源文本匹配。
- `get_concept` 用的 `knowledge.agent_text` 对格式不对或不存在的 id 一律抛 `LookupError`，文字为「未知概念 <id>，不给 id 可取全部列表」；接口 404 仍是「没有这张概念卡：<id>」。

## 页面

v2.6 起前端接入知识层（设计见 [v2.6 §5、§6、§12](../specs/2026-10-28-v2.6-understanding-the-system-design.md)）。

- **「知识」菜单**：主菜单「数据」之后，子项「概念」（`#knowledge`）与「原理」（`#knowledge/principles`）；`#knowledge/<id>[@<版本>]` 是卡片页。
- **概念页 `#knowledge`**：顶部一行「概念卡随代码版本更新，`knowledge-1`；所有规则与 Agent 都未经验证」；过滤框按 id、标题、摘要在前端过滤；按八组分段，每段「分组标题 · n 张」，每张一行：标题（点进卡片页）· id · 摘要 · 适用版本徽标。目录为空显示「还没有概念卡」。
- **卡片页**：标题、分组、`concept:<id>@<版本>`、适用版本、摘要，正文是后端渲染的 HTML（`{@html}` 只用于知识接口返回的内容，原始 HTML 在后端已关闭，卡内链接直接可点）；「相关」与「被这些卡提到」；请求的版本不是当前版时顶部显示 `stale` 说明；id 不存在显示「没有这张概念卡：<id>」与返回列表的链接。
- **原理页 `#knowledge/principles`**：顶部日期选择（默认最近已收盘交易日）；逐节渲染，对应某个批处理阶段的节在正文下方有一个框「当天这一步（<交易日>）」，里面是[运行一览](daily-plan.md)里这一阶段的那一行，并附「在『今日』看完整运行一览」；改日期只重新请求运行一览，不重新加载原理页。原理页不存在显示「还没有原理页」。
- **加载失败（503）**：概念页与卡片页都显示「概念卡加载失败」与后端给的问题清单前 10 条；`ConceptTerm` 照常显示文字和「ⓘ」，点开写「概念卡暂不可用：<原因>」；批处理、运行一览、推理视图、「标的」页不受影响。
- **`ConceptTerm`**：见 [数据菜单 · 就地展开概念](data-menu.md#就地展开概念)；挂载位置在「数据」菜单、推导视图、计划卡、规则基线、版本链、「今日」决定表单、「标的」页、运行一览与推理阅读视图，详见 [每日计划](daily-plan.md)。

## 我的知识

v2.7 新增（`my-knowledge-1`，设计见 [v2.7 spec §5、§6、§7](../specs/2026-10-28-v2.7-growth-loop-design.md)）：**你审定过的经验**。概念卡讲「系统里的东西是什么意思」，我的知识记「你从自己的交易里学到了什么」，只追加、带版本、挂在概念卡或标的上，Agent 生成或复查计划时用工具 `get_knowledge` 读取（[Agent 轨道](agent-track.md)）。所有经验都**未经验证**，表现回看只是描述性统计。

- **条目与版本**：一条知识 = `knowledge_items` 一行 + 第 1 版起连续编号的 `knowledge_versions`，新版 `supersedes_id` 指向上一版。字段：标题（1–60 字）、正文（1–4000 字，纯文本，按段落显示，不渲染 Markdown）、适用范围（1–500 字，必填）、反例（0–1000 字，你手写时可空）、挂靠（`concept:<id>@<版本>` 与标的合计 1–10 处，写入时概念取卡的当前版，标的过 `identity.stock_id`）。`created_at` 是数据库写入时刻，不能补登。来源 `origin`：`manual`（你写的）、`review_card`（复盘卡审定采纳的）、`discussion`（讨论草稿审定采纳的）。
- **归档与恢复**：`knowledge_status` 里的一行记录（`archived` / `active`，带 0–300 字理由），不出新版本；当前状态 = 最后一条，没有记录就是有效。已归档的条目不能出新版（409 `knowledge_archived`，先恢复）。
- **改版的并发**：新版必须带 `base_version`（页面加载时的最高版本），否则 409 `knowledge_stale: 这条知识已更新到第 n 版，请刷新后再改`；写入时持 advisory lock `knowledge:<id>`，两个并发改版只有一个成功。所有写入带 `request_id`：同内容再提交返回原行（`status: replayed`），不同内容 409 `request_id_conflict`。
- **引用**：`know:<id>@<版本>`（正则 `^know:([1-9][0-9]*)(?:@([1-9][0-9]*))?$`），Agent 引用必须带版本，页面地址 `#knowledge/mine/<id>[@<版本>]`。
- **截止时间可见性**：`knowledge_items.visible(versions, statuses, cutoff=…)` 是唯一的判定函数——取 `created_at ≤ cutoff` 的最高版本，截止时间时已归档的不返回，之后写的版本、之后的归档都看不到；`get_knowledge`、页面和 `my_knowledge check-cutoff` 都用它。
- **草稿不可被 Agent 读到**：复盘卡的教训草稿和讨论里的经验草稿存在 `knowledge_drafts`，没有任何 Agent 工具查询它们；只有你审定通过才在 `knowledge_versions` 写第 1 版。讨论草稿在「我的知识 → 草稿」审定，卡的草稿只能在卡上审定（409 `review_on_card`）；采纳有 `target` 的草稿 = 目标条目的新版（`base_version` 规则同上，缺省取 `target_version`）。目标之后又有了新版时，草稿基于旧版、刷新也没用，采纳返回 409 `knowledge_stale: 草稿基于第 k 版，现在是第 n 版：只能不采纳，或在讨论里基于第 n 版重新改写`；草稿视图带 `target_current_version`（目标现在的最高版本），页面据此对这样的草稿禁用「采纳」并写同一句提示，只留「不采纳」。显式带 `base_version` = 最高版本的 API 调用仍可采纳（有意在新版上改写）。同一 `request_id` 同内容重放返回原结果（`status: replayed`，采纳时比较完整内容），同一 id 内容或动作不同 409 `request_id_conflict`；已被另一个请求审定的草稿 409 `draft_decided`（spec §12 的错误码表没有「草稿已审定」，这是实现新增的码，页面文案「这条草稿已经审定过了」）。
- **被引用与表现回看**：计划（首版、修订）和复查通过校验后，引用的知识版本按字段写进 `knowledge_citations`（与计划同一事务）。回看（`knowledge-perf-1`，`my_knowledge.performance`）的样本单位是单元：同一条链修订三次都引用它只算一个样本，取最早引用的那一版，用 `plan-shadow-1` 模拟，汇总用 `review.shadow_block`（与「计划影子」的 `agent_summary` 同一函数、同一阈值）；有结果的不足 5 个不给均值；只在复查里被引用的次数单列。
- **页面**：「知识」菜单的第三项「我的知识」（`#knowledge/mine`，列表与筛选、`mine/new` 写一条、`mine/drafts` 草稿、`mine/<id>[@<版本>]` 条目页：全文、版本历史、被引用、表现回看，旧版有提示）；概念卡页多一段「挂在这张卡上的我的知识」；「标的」页「我的知识」一段。
- **接口**：`GET/POST /api/workbench/my-knowledge`（列表 `?ticker=&concept=&status=active|archived|all`、新条目）、`GET /api/workbench/my-knowledge/{id}[?version=]`（`item`、`versions`、`status_history`、`citations`、`current`）、`POST …/{id}/versions`、`POST …/{id}/status`（body `{request_id, status, reason}`，状态不变 409 `status_unchanged`）、`GET …/{id}/performance`、`GET /api/workbench/my-knowledge-drafts?status=open|all`、`POST /api/workbench/my-knowledge-drafts/{id}/verdict`；`GET /api/workbench/instruments/{ticker}` 多一个 `knowledge`；`GET /api/workbench/chains/…` 每个版本与 `GET /api/workbench/today` 单元的 `agent` 块多一个 `knowledge_refs`（`[{ref, title, fields, current_version}]`）。迁移 027 未执行 503「迁移 027 未执行」（`today` / 链视图的 `knowledge_refs` 为空列表，「标的」页的 `knowledge` 为 `null` 并在 `errors.knowledge` 说明）。挂概念时概念卡目录读不出 → 503「概念卡不可用：…」，只挂标的的照常。
- **命令行** `python -m investment_assistant.workbench.my_knowledge`：`show --id N [--version V]`（只读，没有这条知识退出码 1）；`check-cutoff --since D`（只读）：对 `since` 之后每个成功的 `get_knowledge` 调用，按它所属运行的 `cutoff_at` 用 `visible` 重算一次，返回的每条都必须可见、条数是截止时间下应有的数；对 `knowledge_citations` 每一行，所属运行里必须有一次 `get_knowledge` 返回过这个 `know:<id>@<版本>`，且该版本写于运行截止时间之前；有问题退出码 1，数据库不可用或迁移 027 未执行退出码 2。
- **怎么验证一次**：手写一条挂到一个概念和一个标的 → 改一版（第 2 版指向第 1 版）→ 归档 → 恢复；对挂了知识的标的的一条有效链点「重新复查」，推理视图里能看到 `get_knowledge` 的调用与返回的版本；`my_knowledge check-cutoff --since <上线日>` 通过。
- **已知限制**：知识正文是纯文本；概念卡改版后，知识上挂的 `concept:<id>@<旧版>` 不自动更新（条目页提示挂的是第几版）；表现回看只看影子，不区分你是否真的照做；样本小且不同单元常同日重叠。
