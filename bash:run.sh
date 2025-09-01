#!/usr/bin/env bash
set -euo pipefail

# 说明：
# - 该脚本适用于 Ubuntu 22.04
# - 将自动创建 Python 虚拟环境并安装 ccxt / requests
# - 会导出企业微信机器人 URL（你提供的链接）
# - Telegram 配置可按需填写（若未填写则仅推送企业微信）

# 1) Python 依赖检查
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python3 is required but not found. Please install Python3."
  exit 1
fi

# 2) 进入脚本所在目录
cd "$(dirname "$0")"

# 3) 创建虚拟环境
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# 4) 升级 pip 并安装依赖
pip install --upgrade pip
pip install "ccxt>=4.0.0" requests

# 5) 环境变量（按需修改）
# 企业微信机器人 Webhook（已按你的要求固定）
export WECOM_WEBHOOK_URL=""

# Telegram（如无需 Telegram 推送可留空）
# export TELEGRAM_BOT_TOKEN="your_telegram_bot_token"
# export TELEGRAM_CHAT_ID="your_telegram_chat_id"

# 6) 可选性能/测试参数（按需修改）
# 每轮轮询周期（秒，默认 60）
# export LOOP_INTERVAL="60"
# 每次请求之间的轻微延时（秒，默认 0.15）
# export REQUEST_GAP="0.15"
# 限制监控的代币数量（默认 0 表示不限制，测试时可设置为 30）
# export SYMBOLS_LIMIT="30"

# 7) 启动监控程序
echo "Starting monitor ..."
exec python3 crypto_monitor.py
