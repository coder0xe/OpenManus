from __future__ import annotations

import functools
import hashlib
import json
import time
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterable, Optional

from app.observability.tool_context import get_trace_context

try:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode
except Exception:  # pragma: no cover - optional dependency
    trace = None

    class StatusCode:
        ERROR = "ERROR"
        OK = "OK"

    class Status:
        def __init__(self, status_code: str, description: str | None = None):
            self.status_code = status_code
            self.description = description


class _NoopSpan:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def set_attribute(self, key: str, value: Any) -> None:
        return None

    def record_exception(self, exc: BaseException) -> None:
        return None

    def set_status(self, status: Any) -> None:
        return None


class _NoopTracer:
    @contextmanager
    def start_as_current_span(self, name: str):
        yield _NoopSpan()


def get_tracer(name: str):
    if trace is None:
        return _NoopTracer()
    return trace.get_tracer(name)


_MAX_ATTR_LEN = 2000


def preview_text(value: Any, limit: int = 1000) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        text = value.decode(errors="replace")
    elif isinstance(value, str):
        text = value
    else:
        text = str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."



def hash_text(value: Any) -> str:
    return hashlib.sha1(preview_text(value, limit=100_000).encode("utf-8")).hexdigest()



def safe_json_dumps(value: Any, limit: int = _MAX_ATTR_LEN) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)
    except Exception:
        text = str(value)
    return preview_text(text, limit=limit)



def _normalize_attr(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (bool, int, float, str)):
        return preview_text(value, _MAX_ATTR_LEN) if isinstance(value, str) else value
    if isinstance(value, (list, tuple, set)):
        return preview_text(safe_json_dumps(list(value)), _MAX_ATTR_LEN)
    if isinstance(value, dict):
        return preview_text(safe_json_dumps(value), _MAX_ATTR_LEN)
    return preview_text(value, _MAX_ATTR_LEN)



def set_span_attributes(span: Any, attrs: Dict[str, Any]) -> None:
    for key, value in attrs.items():
        normalized = _normalize_attr(value)
        if normalized is None:
            continue
        try:
            span.set_attribute(key, normalized)
        except Exception:
            continue



def result_to_attributes(result: Any, prefix: str = "tool.result") -> Dict[str, Any]:
    attrs: Dict[str, Any] = {
        f"{prefix}.type": type(result).__name__ if result is not None else "NoneType",
        f"{prefix}.is_none": result is None,
    }
    if result is None:
        return attrs

    if hasattr(result, "output") or hasattr(result, "error"):
        output = getattr(result, "output", None)
        error = getattr(result, "error", None)
        system = getattr(result, "system", None)
        base64_image = getattr(result, "base64_image", None)
        attrs.update(
            {
                f"{prefix}.success": not bool(error),
                f"{prefix}.output_preview": preview_text(output),
                f"{prefix}.output_len": len(str(output)) if output is not None else 0,
                f"{prefix}.output_hash": hash_text(output) if output is not None else None,
                f"{prefix}.error": preview_text(error),
                f"{prefix}.system": preview_text(system),
                f"{prefix}.has_base64_image": bool(base64_image),
                f"{prefix}.base64_image_len": len(base64_image) if base64_image else 0,
            }
        )
        return attrs

    attrs.update(
        {
            f"{prefix}.preview": preview_text(result),
            f"{prefix}.len": len(str(result)),
            f"{prefix}.hash": hash_text(result),
        }
    )
    return attrs



def record_exception(span: Any, exc: BaseException) -> None:
    try:
        span.record_exception(exc)
    except Exception:
        pass
    try:
        span.set_status(Status(StatusCode.ERROR, str(exc)))
    except Exception:
        pass
    set_span_attributes(
        span,
        {
            "error": True,
            "error.type": type(exc).__name__,
            "error.message": preview_text(exc),
        },
    )



def traced_async(
    span_name: str,
    *,
    attr_getter: Optional[Callable[..., Dict[str, Any]]] = None,
    result_getter: Optional[Callable[[Any], Dict[str, Any]]] = None,
):
    def decorator(func):
        tracer = get_tracer(func.__module__)

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            started_at = time.perf_counter()
            with tracer.start_as_current_span(span_name) as span:
                set_span_attributes(span, get_trace_context())
                if attr_getter is not None:
                    try:
                        set_span_attributes(span, attr_getter(*args, **kwargs) or {})
                    except Exception as exc:
                        record_exception(span, exc)
                try:
                    result = await func(*args, **kwargs)
                except Exception as exc:
                    record_exception(span, exc)
                    set_span_attributes(
                        span,
                        {"duration_ms": round((time.perf_counter() - started_at) * 1000, 3)},
                    )
                    raise
                set_span_attributes(
                    span,
                    {
                        "duration_ms": round((time.perf_counter() - started_at) * 1000, 3),
                    },
                )
                if result_getter is not None:
                    try:
                        set_span_attributes(span, result_getter(result) or {})
                    except Exception as exc:
                        record_exception(span, exc)
                else:
                    set_span_attributes(span, result_to_attributes(result))
                try:
                    span.set_status(Status(StatusCode.OK))
                except Exception:
                    pass
                return result

        return wrapper

    return decorator
