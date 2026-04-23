#!/usr/bin/env python3
import os
import runpy
import sys
from pathlib import Path

from phoenix.otel import register


REPO_DIR = Path(
    os.environ.get("OPENMANUS_REPO", "~/agent-stack/agents/OpenManus")
).expanduser().resolve()

ENTRY_MAP = {
    "main": "main.py",
    "mcp": "run_mcp.py",
    "flow": "run_flow.py",
}

mode = sys.argv[1] if len(sys.argv) > 1 else "main"
entry_name = ENTRY_MAP.get(mode, mode)
entry_path = (REPO_DIR / entry_name).resolve()

if not entry_path.exists():
    raise SystemExit(f"入口文件不存在: {entry_path}")

register(
    project_name=os.environ.get("PHOENIX_PROJECT_NAME", "openmanus-workflow"),
    endpoint=os.environ.get(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "http://127.0.0.1:6006/v1/traces",
    ),
    protocol=os.environ.get("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", "http/protobuf"),
    batch=False,
)

os.chdir(REPO_DIR)

if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

sys.argv = [str(entry_path), *sys.argv[2:]]

runpy.run_path(str(entry_path), run_name="__main__")
