# scripts/

| 脚本 | 用途 | 运行 |
|------|------|------|
| `start_local.py` | 本机服务入口：读取仓库 `.env`，只监听 `127.0.0.1`；由根目录 `start.command` 调用，本机 LaunchAgent 登录时启动它 | `./start.command` |
| `verify_cross_market.py` | 美股研究数据的只读验收：只读事务 + HTTP GET，不采集、不迁移、不通知 | `.venv/bin/python scripts/verify_cross_market.py --help` |
| `siwc_precheck.py` | v2.8 开工前核查：Sign in with ChatGPT 登录 → `GET /v1/models` → Responses 调用（含函数工具与被拒字段探测）→ 打印脱敏摘要；**只由你本人执行一次** | 见下文 |
| `archive_v2_docs.py` | 一次性（v3 plan Task 1，已执行）：把 v2 的 `docs/roadmap/`、`docs/features/`、`docs/architecture.md` 移入 `docs/archive/v2/`，并改写所有 Markdown 链接与概念卡 `sources` 路径 | 无需再运行 |

本机服务的启动、重启与日志见 [docs/operations/local-service.md](../docs/operations/local-service.md)。

## v2.8 开工前核查（`siwc_precheck.py`）

v2.8 的 Agent 改用你的 ChatGPT 订阅（[spec](../docs/specs/2026-10-29-v2.8-daily-workflow-design.md) §8）。实现之前要先确认三件事：你的账号能登录并开启了计划用量共享、能看到哪些模型、`langchain-openai` 的 Responses 请求哪些字段会被拒。这个脚本把它们走一遍，结果贴进 spec §17，模型层按那一节选实现路径。

**需要什么**

- 运行服务的这台 Mac（回调只能回到打开浏览器的那台机器），默认浏览器里能登录你的 ChatGPT（Plus / Pro）。
- 仓库的 `.venv`（已装 `openai`、`langchain-openai`）；不需要数据库、不需要启动服务、不读 `.env`。
- 网络能访问 `auth.openai.com` 与 `api.openai.com`。走本机代理的话，在同一个终端里先设好 `https_proxy` / `http_proxy`（与 LaunchAgent plist 里的值相同）；脚本只读环境变量，不改任何配置。

**运行**

```bash
.venv/bin/python scripts/siwc_precheck.py
```

- 脚本会在 `127.0.0.1` 上起一个临时回调端口、在默认浏览器里打开 ChatGPT 授权页；你在浏览器里登录并同意后，回到终端等它跑完（大约 1–3 分钟，会发十几次很小的请求，消耗极少的订阅额度）。
- 浏览器没有自动打开时加 `--no-browser`，把终端打印的授权地址复制到浏览器（地址里没有任何令牌）。
- 想指定模型：`--model <slug>`（默认取可见清单的第一个）；不想做被拒字段的逐个探测：`--skip-probes`。
- 结束时会问「撤销 refresh token 并删除本地令牌文件？」，默认是（直接回车）。想之后再跑一次可以选 `n` 保留，下次加 `--reuse` 免登录；用完务必再跑一次并选删除，或手动删 `.local/siwc-precheck/tokens.json`。

**安全**

- 令牌只写到 `.local/siwc-precheck/tokens.json`（目录 0700、文件 0600，`.local/` 不入库），任何输出都不含令牌；邮箱只显示首字母与域名，`client_id` 只显示前 4 个字符。
- `.local/siwc-precheck/client.json` 保存这次动态注册得到的 `client_id` 与 `host_id`（不是令牌），以后正式接入时不复用它们（服务会自己注册）。
- 每一步失败都会打印一行可读原因（HTTP 状态 + 安全错误码，不含响应原文）并继续下一步；设 `SIWC_PRECHECK_DEBUG=1` 会额外打印脱敏后的堆栈。

**输出怎么贴回 spec**

脚本最后打印「核查摘要：整段贴到 spec §17 末尾」，下面是一段 Markdown（同时写入 `.local/siwc-precheck/summary.md`），按 C1–C8 列出：登录与令牌响应的形状、可见模型清单、最小调用的事件序列与 `usage`、逐个字段的接受 / 拒绝、三种工具形状的结果、`langchain-openai` 三个变体（原样、developer 角色、改写后）的请求体键与两轮工具循环结果，以及建议的路径。把整段原样追加到 `docs/specs/2026-10-29-v2.8-daily-workflow-design.md` §17 表格之后（只追加，不改上面的结构），并在 C8 一行写下你确认的路径。贴之前扫一眼：里面不应出现任何以 `eyJ` 开头的长串或 `Bearer` 后跟内容。
