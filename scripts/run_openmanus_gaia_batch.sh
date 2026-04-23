#!/usr/bin/env bash
set -Eeuo pipefail

# ============================================================
# OpenManus + GAIA 批量运行脚本（固定路径版，默认开启 tracing）
# 适配当前机器目录：
#   ~/agent-stack/.venv-openmanus
#   ~/agent-stack/agents/OpenManus
#   ~/agent-stack/benchmarks/GAIA
#   ~/agent-stack/runs/openmanus-gaia
#   ~/agent-stack/scripts/run_openmanus_gaia_batch.py
#
# 用法示例：
#   bash ~/agent-stack/scripts/run_openmanus_gaia_traced_fixed.sh
#   bash ~/agent-stack/scripts/run_openmanus_gaia_traced_fixed.sh --limit 3
#   bash ~/agent-stack/scripts/run_openmanus_gaia_traced_fixed.sh --levels 1 2
#   USE_SANDBOX=1 bash ~/agent-stack/scripts/run_openmanus_gaia_traced_fixed.sh --limit 3
#   DATASET_CONFIG=2023_level1 bash ~/agent-stack/scripts/run_openmanus_gaia_traced_fixed.sh --split validation --limit 3
# ============================================================

ROOT="$HOME/agent-stack"
VENV="$ROOT/.venv-openmanus"
REPO="$ROOT/agents/OpenManus"
PY_RUNNER="$ROOT/scripts/run_openmanus_gaia_batch.py"
ATTACHMENTS_ROOT="$ROOT/benchmarks/GAIA"
OUTPUT_DIR="$ROOT/runs/openmanus-gaia"

# -----------------------------
# 固定默认参数
# -----------------------------
DATASET="${DATASET:-gaia-benchmark/GAIA}"
DATASET_CONFIG="${DATASET_CONFIG:-2023_all}"
SPLIT="${SPLIT:-validation}"
PHOENIX_PROJECT="${PHOENIX_PROJECT:-openmanus-gaia}"
OTLP_ENDPOINT="${OTLP_ENDPOINT:-http://127.0.0.1:6006/v1/traces}"
USE_SANDBOX="${USE_SANDBOX:-0}"

# tracing 强制开启
ENABLE_TRACE=1

mkdir -p "$OUTPUT_DIR"

[[ -d "$ROOT" ]] || { echo "[ERROR] 目录不存在: $ROOT"; exit 1; }
[[ -d "$REPO" ]] || { echo "[ERROR] OpenManus 仓库不存在: $REPO"; exit 1; }
[[ -d "$VENV" ]] || { echo "[ERROR] 虚拟环境不存在: $VENV"; exit 1; }
[[ -f "$PY_RUNNER" ]] || { echo "[ERROR] Python runner 不存在: $PY_RUNNER"; exit 1; }

# shellcheck disable=SC1090
source "$VENV/bin/activate"

CMD=(
  python "$PY_RUNNER"
  --repo-root "$REPO"
  --dataset "$DATASET"
  --dataset-config "$DATASET_CONFIG"
  --split "$SPLIT"
  --attachments-root "$ATTACHMENTS_ROOT"
  --output-dir "$OUTPUT_DIR"
  --enable-trace
  --phoenix-project "$PHOENIX_PROJECT"
  --otlp-endpoint "$OTLP_ENDPOINT"
)

if [[ "$USE_SANDBOX" == "1" ]]; then
  CMD+=(--sandbox)
fi

# 透传额外参数，例如：--limit 3 --levels 1 2 --max-steps 20
CMD+=("$@")

echo "[INFO] 使用虚拟环境: $VENV"
echo "[INFO] OpenManus 仓库: $REPO"
echo "[INFO] 数据集: $DATASET"
echo "[INFO] 数据集配置: $DATASET_CONFIG"
echo "[INFO] split: $SPLIT"
echo "[INFO] 输出目录: $OUTPUT_DIR"
echo "[INFO] Phoenix Project: $PHOENIX_PROJECT"
echo "[INFO] OTLP Endpoint: $OTLP_ENDPOINT"
echo "[INFO] Sandbox 模式: $USE_SANDBOX"
echo "[INFO] Tracing: 强制开启"
echo
printf '[INFO] 执行命令:\n%s\n\n' "${CMD[*]}"

exec "${CMD[@]}"
