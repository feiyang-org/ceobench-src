#!/bin/bash
# 7-day DeepSeek V4 Flash smoke run (thinking off).
# Usage: bash scripts/start_deepseek_flash_smoke.sh official|opencode

set -euo pipefail
cd "$(dirname "$0")/.."

GATEWAY="${1:-official}"
case "$GATEWAY" in
  official)
    PROVIDER="deepseek"
    ;;
  opencode)
    PROVIDER="opencode"
    ;;
  *)
    echo "Usage: $0 official|opencode" >&2
    exit 1
    ;;
esac

echo "Starting 7-day DeepSeek V4 Flash smoke run via $GATEWAY ($PROVIDER)..."
exec uv run python -m saas_bench.agents.bash_agent.run_test \
  --provider "$PROVIDER" \
  --model deepseek-v4-flash \
  --reasoning-effort none \
  --seed 42 \
  --days 7 \
  --workspace deepseek_runs
