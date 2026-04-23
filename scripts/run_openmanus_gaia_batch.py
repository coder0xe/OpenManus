#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from datasets import load_dataset, load_from_disk


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量运行 OpenManus on GAIA")

    parser.add_argument(
        "--repo-root",
        type=str,
        default=str(Path.home() / "agent-stack/agents/OpenManus"),
        help="OpenManus 仓库根目录",
    )
    parser.add_argument(
        "--dataset",
        type=str,
        default="gaia-benchmark/GAIA",
        help="Hugging Face 数据集名，或本地 json/jsonl/csv/parquet 文件，或 load_from_disk 目录",
    )
    parser.add_argument(
        "--dataset-config",
        type=str,
        default="2023_all",
        help="GAIA dataset config，例如 2023_all / 2023_level1 / 2023_level2 / 2023_level3",
    )
    parser.add_argument(
        "--split",
        type=str,
        default="validation",
        help="数据集 split，例如 validation",
    )
    parser.add_argument(
        "--attachments-root",
        type=str,
        default=str(Path.home() / "agent-stack/benchmarks/GAIA"),
        help="GAIA 附件根目录；若样本包含 file_name，会自动拼接该路径",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(Path.home() / "agent-stack/runs/openmanus-gaia"),
        help="输出目录",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="最多运行多少条，0 表示不限制",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="从第几条开始",
    )
    parser.add_argument(
        "--levels",
        type=int,
        nargs="*",
        default=None,
        help="只运行指定难度等级，如 --levels 1 2",
    )
    parser.add_argument(
        "--task-ids",
        type=str,
        nargs="*",
        default=None,
        help="只运行指定 task_id，可传多个",
    )
    parser.add_argument(
        "--sandbox",
        action="store_true",
        help="使用 SandboxManus 而不是普通 Manus",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help="覆盖 agent 最大步数",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.0,
        help="每条样本结束后 sleep 多久",
    )
    parser.add_argument(
        "--enable-trace",
        action="store_true",
        help="启用 Phoenix / OTel tracing",
    )
    parser.add_argument(
        "--phoenix-project",
        type=str,
        default="openmanus-gaia",
        help="Phoenix project 名称",
    )
    parser.add_argument(
        "--otlp-endpoint",
        type=str,
        default="http://127.0.0.1:6006/v1/traces",
        help="OTLP traces endpoint",
    )
    parser.add_argument(
        "--otlp-protocol",
        type=str,
        default="http/protobuf",
        help="OTLP traces protocol",
    )

    return parser.parse_args()


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().lower().split())


def extract_first(mapping: dict[str, Any], keys: Iterable[str], default: Any = None) -> Any:
    lowered = {str(k).lower(): v for k, v in mapping.items()}
    for key in keys:
        if key in mapping:
            return mapping[key]
        lk = str(key).lower()
        if lk in lowered:
            return lowered[lk]
    return default


def record_level(example: dict[str, Any]) -> Optional[int]:
    raw = extract_first(example, ["Level", "level"], None)
    if raw is None:
        return None
    try:
        return int(raw)
    except Exception:
        try:
            return int(str(raw).strip())
        except Exception:
            return None


def record_task_id(example: dict[str, Any]) -> Optional[str]:
    raw = extract_first(example, ["task_id", "id", "Task ID"], None)
    return None if raw is None else str(raw)


def attach_local_file_hint(
    prompt: str,
    example: dict[str, Any],
    attachments_root: Optional[Path],
) -> tuple[str, Optional[str]]:
    if not attachments_root:
        return prompt, None

    fname = extract_first(example, ["file_name", "filename", "File_name"], None)
    if not fname:
        return prompt, None

    attachment_path = (attachments_root / str(fname)).resolve()
    if attachment_path.exists():
        prompt = (
            f"{prompt}\n\n"
            f"本题提供了本地附件文件，请优先使用它。\n"
            f"附件绝对路径：{attachment_path}\n"
        )
        return prompt, str(attachment_path)

    return prompt, str(attachment_path)


def load_any_dataset(dataset_spec: str, dataset_config: str, split: str):
    path = Path(dataset_spec).expanduser()

    if path.exists():
        if path.is_dir():
            ds = load_from_disk(str(path))
            if hasattr(ds, "keys"):
                if split in ds:
                    return ds[split]
                keys = list(ds.keys())
                if len(keys) == 1:
                    return ds[keys[0]]
                raise ValueError(f"本地数据目录是 DatasetDict，但找不到 split={split}，可用 splits={keys}")
            return ds

        suffix = path.suffix.lower()
        if suffix in {".json", ".jsonl"}:
            return load_dataset("json", data_files=str(path), split="train")
        if suffix == ".csv":
            return load_dataset("csv", data_files=str(path), split="train")
        if suffix == ".parquet":
            return load_dataset("parquet", data_files=str(path), split="train")

        raise ValueError(f"不支持的本地数据文件格式: {path}")

    # Hugging Face 数据集
    return load_dataset(dataset_spec, dataset_config, split=split)


async def maybe_enable_trace(args: argparse.Namespace) -> None:
    if not args.enable_trace:
        return

    from phoenix.otel import register

    register(
        project_name=args.phoenix_project,
        endpoint=args.otlp_endpoint,
        protocol=args.otlp_protocol,
        batch=False,
    )


def install_repo_path(repo_root: Path) -> None:
    repo_root = repo_root.resolve()
    os.chdir(repo_root)
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))


def import_agent_class(sandbox: bool):
    if not sandbox:
        module = importlib.import_module("app.agent.manus")
        return getattr(module, "Manus")

    candidates = [
        ("app.agent.manus", "SandboxManus"),
        ("app.agent.sandbox", "SandboxManus"),
    ]
    last_err = None
    for module_name, class_name in candidates:
        try:
            module = importlib.import_module(module_name)
            return getattr(module, class_name)
        except Exception as e:
            last_err = e

    raise RuntimeError(f"无法导入 SandboxManus：{last_err}")


async def create_agent(args: argparse.Namespace):
    agent_cls = import_agent_class(args.sandbox)
    agent = await agent_cls.create()
    if args.max_steps is not None and hasattr(agent, "max_steps"):
        setattr(agent, "max_steps", args.max_steps)
    return agent


def extract_final_text(agent_result: Any) -> str:
    if agent_result is None:
        return ""

    if isinstance(agent_result, str):
        return agent_result

    if isinstance(agent_result, dict):
        for key in ["content", "text", "result", "response", "message"]:
            value = agent_result.get(key)
            if value:
                return str(value)

    for attr in ["content", "text", "result", "response", "message"]:
        if hasattr(agent_result, attr):
            value = getattr(agent_result, attr)
            if value:
                return str(value)

    return str(agent_result)


def extract_last_assistant_message(agent: Any) -> str:
    try:
        messages = getattr(getattr(agent, "memory", None), "messages", None)
        if not messages:
            return ""

        for msg in reversed(messages):
            if isinstance(msg, dict):
                role = msg.get("role")
                content = msg.get("content", "")
                if role == "assistant" and content:
                    return str(content)

            role = getattr(msg, "role", None)
            content = getattr(msg, "content", "")
            if role == "assistant" and content:
                return str(content)
    except Exception:
        return ""

    return ""


async def run_one(
    args: argparse.Namespace,
    example: dict[str, Any],
    attachments_root: Optional[Path],
) -> dict[str, Any]:
    question = extract_first(example, ["Question", "question", "prompt", "instruction"], "")
    gold = extract_first(example, ["Final answer", "final answer", "final_answer", "answer"], "")
    task_id = record_task_id(example)
    level = record_level(example)

    prompt, attachment_path = attach_local_file_hint(str(question), example, attachments_root)

    started = time.time()
    status = "ok"
    error = None
    error_traceback = None
    result_raw: Any = None
    result_text = ""
    final_assistant_message = ""

    try:
        agent = await create_agent(args)
        result_raw = await agent.run(prompt)
        result_text = extract_final_text(result_raw)
        final_assistant_message = extract_last_assistant_message(agent)
    except Exception as e:
        status = "error"
        error = f"{type(e).__name__}: {e}"
        error_traceback = traceback.format_exc()

    ended = time.time()

    return {
        "task_id": task_id,
        "level": level,
        "question": question,
        "prompt": prompt,
        "attachment_path": attachment_path,
        "gold_answer": gold,
        "agent_result": result_text,
        "final_assistant_message": final_assistant_message,
        "run_status": status,
        "error": error,
        "error_traceback": error_traceback,
        "duration_sec": round(ended - started, 3),
        "normalized_exact_match": normalize_text(result_text) == normalize_text(gold),
    }


def filter_by_levels(ds, levels: Optional[list[int]]):
    if not levels:
        return ds
    wanted = set(levels)
    return ds.filter(lambda x: record_level(dict(x)) in wanted)


def filter_by_task_ids(ds, task_ids: Optional[list[str]]):
    if not task_ids:
        return ds
    wanted = {str(x) for x in task_ids}
    return ds.filter(lambda x: record_task_id(dict(x)) in wanted)


async def amain() -> None:
    args = parse_args()

    repo_root = Path(args.repo_root).expanduser().resolve()
    attachments_root = Path(args.attachments_root).expanduser().resolve() if args.attachments_root else None
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    install_repo_path(repo_root)
    await maybe_enable_trace(args)

    ds = load_any_dataset(args.dataset, args.dataset_config, args.split)
    ds = filter_by_levels(ds, args.levels)
    ds = filter_by_task_ids(ds, args.task_ids)

    total_before_slice = len(ds)

    if args.offset:
        ds = ds.select(range(args.offset, len(ds)))

    if args.limit and args.limit > 0:
        ds = ds.select(range(min(args.limit, len(ds))))

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"gaia_{args.dataset_config}_{args.split}_{ts}"
    jsonl_path = output_dir / f"{stem}.jsonl"
    summary_path = output_dir / f"{stem}_summary.json"

    results: list[dict[str, Any]] = []

    with jsonl_path.open("w", encoding="utf-8") as f:
        for idx, example in enumerate(ds):
            rec = await run_one(args, dict(example), attachments_root)
            rec["index_in_run"] = idx
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            results.append(rec)

            print(
                f"[{idx + 1}/{len(ds)}] "
                f"task_id={rec.get('task_id')} "
                f"status={rec['run_status']} "
                f"em={rec['normalized_exact_match']} "
                f"duration={rec['duration_sec']}s"
            )

            if args.sleep_seconds and idx != len(ds) - 1:
                time.sleep(args.sleep_seconds)

    ok_count = sum(1 for r in results if r["run_status"] == "ok")
    em_count = sum(1 for r in results if r["normalized_exact_match"])

    summary = {
        "dataset": args.dataset,
        "dataset_config": args.dataset_config,
        "split": args.split,
        "total_before_slice": total_before_slice,
        "offset": args.offset,
        "limit": args.limit,
        "levels": args.levels,
        "task_ids": args.task_ids,
        "sandbox": args.sandbox,
        "max_steps": args.max_steps,
        "sleep_seconds": args.sleep_seconds,
        "enable_trace": args.enable_trace,
        "phoenix_project": args.phoenix_project if args.enable_trace else None,
        "run_count": len(results),
        "ok_count": ok_count,
        "error_count": len(results) - ok_count,
        "normalized_exact_match_count": em_count,
        "normalized_exact_match_rate": (em_count / len(results)) if results else 0.0,
        "jsonl_path": str(jsonl_path),
    }

    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n运行完成：")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(amain())
