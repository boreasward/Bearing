# Pi 无人值守运行可行性（v3 spec §10 第 0 步）

v3 用 Pi coding agent 替换本仓库的模型层，需要它在无人值守（无交互终端）下稳定运行。本文记录
2026-11-05 在本机（macOS，Apple Silicon）对 Pi **1.5.0** 的实测结论；十项检查全部通过。
复检脚本与迷你 MCP 服务在 `scripts/pi_precheck/`，提供方与模型 id 只作为命令行参数传入，
不在仓库任何文件里出现。

## 专用配置目录

- 目录固定为 `~/.pi-ia`，由用户手工创建：`mkdir -p ~/.pi-ia && PI_CODING_AGENT_DIR=~/.pi-ia ~/.local/bin/pi`，
  在界面里 `/login` 登录、放入提供方定义（`models.json` + `auth.json`）。
- 运行时用环境变量 `PI_CODING_AGENT_DIR=~/.pi-ia` 指过去，另设 `PI_SKIP_VERSION_CHECK=1` 跳过版本检查请求。
- 环境允许清单（实测够用）：`HOME`、`PATH=$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin`、
  继承的代理变量（名字含 `proxy`）、上面两个 `PI_*` 变量；`stdin` 关闭；每次进程 180 秒超时。

## 实测通过的命令行（一次性运行）

```bash
pi --mode json --no-session --approve --no-extensions -e builtin:mcp \
   --no-skills --no-prompt-templates --no-themes --no-context-files \
   --tools 'mcp__ia__*' --provider <provider> --model <model-id> "<提示词>"
```

各开关的作用：`--mode json` 输出 JSONL 事件流；`--no-session` 不落盘（多轮见下）；
`--approve` 一次性信任项目级 `.pi/`（无人值守下无法弹信任框）；`--no-extensions -e builtin:mcp`
只保留内置 MCP 支持；其余 `--no-*` 关掉技能/模板/主题/上下文文件发现；
`--tools 'mcp__ia__*'` 把工具收窄到只剩 `ia` 服务的 MCP 工具（实测无任何内置工具事件）。

多轮续接（同一工作目录连跑，去掉 `--no-session`）：

```bash
pi --mode json --approve … --session-dir <工作目录>/sessions --session-id <会话id> "<第一条>"
pi --mode json --approve … --session-dir <工作目录>/sessions --session-id <会话id> "<第二条>"
```

launchd 一次性作业（无 TTY）同样可行：

```bash
launchctl submit -l local.ia.pi-precheck -- /bin/sh -c 'export HOME=… PATH=… PI_CODING_AGENT_DIR=… PI_SKIP_VERSION_CHECK=1; cd <工作目录>; pi --mode json … > out.jsonl 2> err.txt'
launchctl remove local.ia.pi-precheck   # 标记文件到位后清理
```

## 项目级 `.pi/mcp.json`

```json
{
  "mcpServers": {
    "ia": {
      "command": "<仓库绝对路径>/.venv/bin/python",
      "args": ["<仓库绝对路径>/scripts/pi_precheck/mini_mcp.py",
               "--session", "2026-11-04", "--log", "<日志绝对路径>"],
      "exposure": "direct"
    }
  }
}
```

`exposure: "direct"` 让工具直接声明给模型（名字 `mcp__ia__<tool>`）；MCP 服务端 `initialize`
响应必须含 `serverInfo.name/version`（Pi 客户端会校验）。注意 `pi mcp list` 不接受 `--approve`，
项目级配置要被它读到需在 `~/.pi-ia/trust.json` 里给该目录留一条已信任记录（复检脚本临时写入、
用完还原；正式运行用 `--approve` 即可，不需要动 trust.json）。

## 事件名（以 Pi 自带 `docs/json.md` 为准，1.5.0）

标准序列：`session` 头记录 → `agent_start` → `turn_start` → `message_start` / `message_update` /
`message_end` → `tool_execution_start` / `tool_execution_end` → `turn_end` → `agent_end` →
`agent_settled`。本次十项检查的判定逻辑实际依赖的字段：

- `message_end`：`message.role`、`message.content[]`（`type:"text"` 的 `text` 拼成最终回复）、
  `message.stopReason`、`message.usage`（`input/output/totalTokens`）。
- `tool_execution_start` / `tool_execution_end`：`toolName`（白名单判定，须以 `mcp__ia__` 开头）。
- MCP 服务端日志：每条请求一行（方法名、工具名、参数），用于确认 `tools/call` 是否真的发生。

## v3.2.1 宿主事件与对话元数据（代码已实现，待上线验证）

`runtime/pi.py` 对外发出的 `tool_start` / `tool_end` 使用去掉 `mcp__ia__` 前缀的裸工具名。
`tool_end.ref` 从 `result.content` 依序扫描文本项，取第一段能解析成 JSON 对象且 `ref` 为
`call:` 开头字符串的信封；空内容、非文本、坏 JSON 或无有效引用时留空，后续事件继续处理，
不使用 Pi 自身的 `toolCallId` 猜编号。`tests/fixtures/pi_events/ok.jsonl` 是手写回放样本，
已补齐工具面的 `{"ref":"call:1","data":…}` 信封；真实 Pi 链路仍按 v3.2.1 上线步骤验证。

对话宿主 `runtime/chat.py::turn` 在用户消息提交后、调用运行时前先发
`RuntimeEvent("start", text=agent_session)`；会话编号是 `chat-{chat_id}-{seq}`，其中 `seq`
是该轮用户消息序号。该帧不进入助手正文或工具引用。首条用户消息去掉首尾空白后取前 30 字
作标题，超过 30 字加「…」，后续轮次保留标题；每次用户与助手消息写入后刷新对话 `updated_at`。
运行时协议的 `start.text` 由页面接口编码成 SSE，帧不由 Pi 进程产生。

## 成败判定信号

- **成功**：退出码 0 ＋ 最后一条 assistant `message_end` 的 `stopReason` 正常（`stop`/`toolUse`）＋
  回复文本含预期值 ＋ MCP 日志出现 `tools/call`。
- **失败**：实测**模型 id 不存在时 pi 仍退出 0**，退出码单独不可靠；可靠信号是最后一条 assistant
  消息的 `stopReason == "error"`（附 `errorMessage`）＋ 服务端无 `tools/call` ＋ 回复缺预期值，
  三者任一出现即判失败（复检脚本 `failure_detected` 项即用这套信号）。

## 一次调用的耗时与用量

mcp_roundtrip（一次工具调用 + 一轮最终回复，2 条 assistant 消息）：**13.2 秒**，
`totalTokens` 合计 **1552**（input 851、output 61，其余为缓存读写）。launchd 下同一调用约 27 秒
（含进程冷启动）。整套十项检查约 75 秒、7 次模型调用。

## 十项检查结果（2026-11-05，全部通过）

| 检查 | 结果 | 证据摘要 |
|---|---|---|
| `binary` | ✅ | 最小环境下 `pi --version` 退出 0，版本 1.5.0 |
| `auth` | ✅ | `pi auth check --provider <provider> --json` 退出 0，`status=ready` |
| `mcp_roundtrip` | ✅ | 最终回复含 `104.25`；服务日志有 `get_price_bars` 的 `tools/call`；`get_session` 回显 `2026-11-04` |
| `tool_allowlist` | ✅ | 工具事件仅 `mcp__ia__get_price_bars`，无内置工具 |
| `no_builtin_effects` | ✅ | 金丝雀文件内容与 `Example Domain` 均未泄漏，回复 `cannot`，无非 `mcp__ia__` 工具事件 |
| `context_isolation` | ✅ | 父目录 `AGENTS.md` 与工作目录 `CLAUDE.md` 的 `CANARY-` 词均未进入回复（回复 `NONE`）；`pi mcp list` 只列 `ia` 一个服务 |
| `usage` | ✅ | 事件流可累计 token：1552 > 0 |
| `failure_detected` | ✅ | 坏模型 id 可判出：`stopReason=error`、无 `tools/call`、预期值缺失（注意退出码仍为 0） |
| `session_continuation` | ✅ | 同 `--session-id` 两次运行，第二次回忆出第一次的随机词；两次退出码 0 |
| `launchd` | ✅ | `launchctl submit` 一次性作业 27 秒跑完，输出含 `104.25`，标记文件保证只跑一次 |

## 尚未验证

- `--mode rpc` 长连接协议（本验证只覆盖 `--mode json` 一次性运行）。
- 长上下文压缩（`compaction_*`）与自动重试（`auto_retry_*`）事件未实测。
- OAuth 类提供方、API key 过期/刷新路径未实测（本次提供方为配置文件内嵌 apiKey）。
- 真实代理链路（脚本实现了代理变量继承，本机无代理变量可继承）。
- `pi update` 升级后的兼容性；长期驻留进程的内存/句柄稳定性。
- 多会话并发（多个 `--session-id` 同时跑）。

复检方法：`.venv/bin/python scripts/pi_precheck/run_checks.py --provider <provider> --model <model-id> --launchd`
（每次约 7 次模型调用，消耗真实额度；结果 JSON 打到 stdout，进度打到 stderr，退出码 0 即十项全过）。
