# scripts/

| 脚本 | 用途 | 运行 |
|------|------|------|
| `start_local.py` | 本机服务入口：读取仓库 `.env`，只监听 `127.0.0.1`；由根目录 `start.command` 调用，本机 LaunchAgent 登录时启动它 | `./start.command` |
| `pi_precheck/mini_mcp.py`、`pi_precheck/run_checks.py` | v3 开工前核查（spec §10 第 0 步）：无依赖迷你 stdio MCP 服务 + Pi 1.5.0 无人值守可行性十项检查；提供方与模型只经 `--provider/--model` 参数传入，结果见 [docs/operations/pi-unattended.md](../docs/operations/pi-unattended.md) | `.venv/bin/python scripts/pi_precheck/run_checks.py --provider <provider> --model <model-id> [--launchd]` |

本机服务的启动、重启与日志见 [docs/operations/local-service.md](../docs/operations/local-service.md)。
