# Copyright (c) Data Agent Team. All rights reserved.
"""Enhanced observability with structured logging and tracing."""

import asyncio
import json
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from loguru import logger


class LogLevel(Enum):
    """Log levels."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class LogEntry:
    """Structured log entry with full context."""

    timestamp: str
    level: str
    message: str
    task_id: Optional[str] = None
    span_id: Optional[str] = None
    step_id: Optional[str] = None
    agent_name: Optional[str] = None
    action_id: Optional[str] = None
    duration_ms: Optional[float] = None
    metadata: dict = field(default_factory=dict)

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "timestamp": self.timestamp,
            "level": self.level,
            "message": self.message,
            "task_id": self.task_id,
            "span_id": self.span_id,
            "step_id": self.step_id,
            "agent_name": self.agent_name,
            "action_id": self.action_id,
            "duration_ms": self.duration_ms,
            "metadata": self.metadata,
        }


class TaskLogger:
    """Structured logger for task execution with full context."""

    def __init__(self, task_id: str):
        self.task_id = task_id
        self._logs: list[LogEntry] = []
        self._start_time = datetime.utcnow()
        self._current_span: Optional[str] = None

    def _create_entry(
        self,
        level: str,
        message: str,
        span_id: str = None,
        step_id: str = None,
        agent_name: str = None,
        action_id: str = None,
        duration_ms: float = None,
        **metadata,
    ) -> LogEntry:
        """Create a log entry with full context."""
        return LogEntry(
            timestamp=datetime.utcnow().isoformat(),
            level=level,
            message=message,
            task_id=self.task_id,
            span_id=span_id or self._current_span,
            step_id=step_id,
            agent_name=agent_name,
            action_id=action_id,
            duration_ms=duration_ms,
            metadata=metadata,
        )

    def set_span(self, span_id: str) -> None:
        """Set current span context."""
        self._current_span = span_id

    def clear_span(self) -> None:
        """Clear current span context."""
        self._current_span = None

    def debug(self, message: str, **kwargs):
        """Log debug message."""
        entry = self._create_entry("debug", message, **kwargs)
        self._logs.append(entry)
        logger.debug(f"[{self.task_id}] {message}")

    def info(self, message: str, **kwargs):
        """Log info message."""
        entry = self._create_entry("info", message, **kwargs)
        self._logs.append(entry)
        logger.info(f"[{self.task_id}] {message}")

    def warning(self, message: str, **kwargs):
        """Log warning message."""
        entry = self._create_entry("warning", message, **kwargs)
        self._logs.append(entry)
        logger.warning(f"[{self.task_id}] {message}")

    def error(self, message: str, **kwargs):
        """Log error message."""
        entry = self._create_entry("error", message, **kwargs)
        self._logs.append(entry)
        logger.error(f"[{self.task_id}] {message}")

    def critical(self, message: str, **kwargs):
        """Log critical message."""
        entry = self._create_entry("critical", message, **kwargs)
        self._logs.append(entry)
        logger.critical(f"[{self.task_id}] {message}")

    def get_logs(self) -> list[dict]:
        """Get all logs as dictionaries."""
        return [entry.to_dict() for entry in self._logs]

    def get_elapsed_time_ms(self) -> float:
        """Get elapsed time since logger creation."""
        return (datetime.utcnow() - self._start_time).total_seconds() * 1000


class ExecutionTracer:
    """Tracer for tracking execution flow."""

    def __init__(self, task_id: str):
        self.task_id = task_id
        self._spans: list[dict] = []
        self._current_span: Optional[dict] = None
        self._span_stack: list[dict] = []

    def start_span(
        self,
        name: str,
        span_type: str = "step",
        parent_id: str = None,
        metadata: dict = None,
    ) -> str:
        """Start a new span."""
        span_id = str(uuid.uuid4())[:8]
        span = {
            "span_id": span_id,
            "name": name,
            "type": span_type,
            "parent_id": parent_id,
            "start_time": datetime.utcnow().isoformat(),
            "end_time": None,
            "duration_ms": None,
            "status": "started",
            "metadata": metadata or {},
        }

        if self._current_span:
            self._span_stack.append(self._current_span)

        self._current_span = span
        self._spans.append(span)

        return span_id

    def end_span(self, span_id: str, status: str = "ok", error: str = None):
        """End a span."""
        span = next((s for s in self._spans if s["span_id"] == span_id), None)
        if not span:
            return

        span["end_time"] = datetime.utcnow().isoformat()
        span["status"] = "error" if error else status

        if span["start_time"]:
            start = datetime.fromisoformat(span["start_time"])
            span["duration_ms"] = (datetime.utcnow() - start).total_seconds() * 1000

        if error:
            span["error"] = error

        # Pop from stack if there's a parent
        if self._span_stack:
            self._current_span = self._span_stack.pop()
        else:
            self._current_span = None

    def add_event(self, span_id: str, event_name: str, metadata: dict = None):
        """Add an event to a span."""
        span = next((s for s in self._spans if s["span_id"] == span_id), None)
        if not span:
            return

        if "events" not in span:
            span["events"] = []

        span["events"].append({
            "name": event_name,
            "timestamp": datetime.utcnow().isoformat(),
            "metadata": metadata or {},
        })

    def get_traces(self) -> list[dict]:
        """Get all spans."""
        return self._spans

    def get_summary(self) -> dict:
        """Get trace summary."""
        total_duration = sum(s.get("duration_ms", 0) for s in self._spans)
        return {
            "task_id": self.task_id,
            "total_spans": len(self._spans),
            "total_duration_ms": total_duration,
            "spans_by_type": self._count_by_type(),
            "error_count": sum(1 for s in self._spans if s.get("status") == "error"),
        }

    def _count_by_type(self) -> dict:
        """Count spans by type."""
        counts = {}
        for span in self._spans:
            span_type = span.get("type", "unknown")
            counts[span_type] = counts.get(span_type, 0) + 1
        return counts


# Context variable for current task logger
_current_logger: ContextVar[Optional[TaskLogger]] = ContextVar("current_logger", default=None)
_current_tracer: ContextVar[Optional[ExecutionTracer]] = ContextVar("current_tracer", default=None)


def get_current_logger() -> Optional[TaskLogger]:
    """Get current task logger from context."""
    return _current_logger.get()


def get_current_tracer() -> Optional[ExecutionTracer]:
    """Get current tracer from context."""
    return _current_tracer.get()


class ObservabilityMiddleware:
    """Middleware for request observability."""

    @staticmethod
    def create_logger(task_id: str) -> TaskLogger:
        """Create logger and set in context."""
        task_logger = TaskLogger(task_id)
        _current_logger.set(task_logger)
        return task_logger

    @staticmethod
    def create_tracer(task_id: str) -> ExecutionTracer:
        """Create tracer and set in context."""
        tracer = ExecutionTracer(task_id)
        _current_tracer.set(tracer)
        return tracer

    @staticmethod
    def clear():
        """Clear context."""
        _current_logger.set(None)
        _current_tracer.set(None)


class MetricsCollector:
    """Collects and reports metrics."""

    def __init__(self):
        self._counters: dict[str, int] = {}
        self._histograms: dict[str, list[float]] = {}
        self._lock = asyncio.Lock()

    async def increment(self, metric: str, value: int = 1):
        """Increment a counter."""
        async with self._lock:
            self._counters[metric] = self._counters.get(metric, 0) + value

    async def record_duration(self, metric: str, duration_ms: float):
        """Record a duration."""
        async with self._lock:
            if metric not in self._histograms:
                self._histograms[metric] = []
            self._histograms[metric].append(duration_ms)

    async def get_metrics(self) -> dict:
        """Get all metrics."""
        async with self._lock:
            return {
                "counters": self._counters.copy(),
                "histograms": {
                    k: {
                        "count": len(v),
                        "min": min(v) if v else 0,
                        "max": max(v) if v else 0,
                        "avg": sum(v) / len(v) if v else 0,
                    }
                    for k, v in self._histograms.items()
                },
            }

    async def reset(self):
        """Reset all metrics."""
        async with self._lock:
            self._counters.clear()
            self._histograms.clear()
