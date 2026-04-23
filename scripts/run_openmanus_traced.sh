#!/usr/bin/env bash
set -Eeuo pipefail

MODE="${1:-main}"

export OPENMANUS_REPO="${OPENMANUS_REPO:-$HOME/agent-stack/agents/OpenManus}"
export PHOENIX_PROJECT_NAME="${PHOENIX_PROJECT_NAME:-openmanus-workflow}"
export OTEL_SERVICE_NAME="${OTEL_SERVICE_NAME:-openmanus-agent}"
export OTEL_EXPORTER_OTLP_TRACES_PROTOCOL="${OTEL_EXPORTER_OTLP_TRACES_PROTOCOL:-http/protobuf}"
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="${OTEL_EXPORTER_OTLP_TRACES_ENDPOINT:-http://127.0.0.1:6006/v1/traces}"

source "$HOME/agent-stack/.venv-openmanus/bin/activate"
python "$HOME/agent-stack/scripts/openmanus_traced_launcher.py" "$MODE"
