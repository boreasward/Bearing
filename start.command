#!/bin/zsh
set -eu

cd -- "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
  print -u2 '缺少 Python 环境，请先按 README 安装依赖。'
  exit 1
fi

# 本地入口先加载 .env 再导入服务，避免服务在配置就绪前读取环境变量。
exec .venv/bin/python scripts/start_local.py
