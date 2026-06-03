# Copyright (c) Data Agent Team. All rights reserved.
"""Resource monitoring and dynamic concurrency control."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from loguru import logger

# Optional dependencies
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    psutil = None

try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False
    torch = None


class ResourceStatus(Enum):
    """Resource status levels."""

    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class ResourceSnapshot:
    """Snapshot of system resources."""

    timestamp: datetime
    cpu_percent: float
    memory_used_gb: float
    memory_total_gb: float
    memory_percent: float
    gpu_memory_used_mb: float = 0
    gpu_memory_total_mb: float = 0
    gpu_utilization: float = 0
    active_tasks: int = 0


class ResourceMonitor:
    """Monitor system resources and provide capacity information."""

    def __init__(
        self,
        memory_warning_threshold: float = 0.80,
        memory_critical_threshold: float = 0.90,
        cpu_warning_threshold: float = 0.75,
        cpu_critical_threshold: float = 0.90,
    ):
        self.memory_warning_threshold = memory_warning_threshold
        self.memory_critical_threshold = memory_critical_threshold
        self.cpu_warning_threshold = cpu_warning_threshold
        self.cpu_critical_threshold = cpu_critical_threshold

        self._current_tasks = 0
        self._max_concurrent_tasks = 10
        self._lock = asyncio.Lock()

    async def get_snapshot(self) -> ResourceSnapshot:
        """Get current resource snapshot."""
        if not PSUTIL_AVAILABLE:
            # Return default values when psutil is not available
            return ResourceSnapshot(
                timestamp=datetime.utcnow(),
                cpu_percent=0.0,
                memory_used_gb=0.0,
                memory_total_gb=0.0,
                memory_percent=0.0,
                active_tasks=self._current_tasks,
            )

        memory = psutil.virtual_memory()
        cpu_percent = psutil.cpu_percent(interval=0.1)

        snapshot = ResourceSnapshot(
            timestamp=datetime.utcnow(),
            cpu_percent=cpu_percent,
            memory_used_gb=memory.used / (1024**3),
            memory_total_gb=memory.total / (1024**3),
            memory_percent=memory.percent,
            active_tasks=self._current_tasks,
        )

        # Try to get GPU info if available
        if TORCH_AVAILABLE and torch.cuda.is_available():
            try:
                gpu_memory = torch.cuda.memory_allocated() / (1024**2)
                gpu_total = torch.cuda.get_device_properties(0).total_memory / (1024**2)
                snapshot.gpu_memory_used_mb = gpu_memory
                snapshot.gpu_memory_total_mb = gpu_total
                snapshot.gpu_utilization = (gpu_memory / gpu_total) * 100 if gpu_total > 0 else 0
            except Exception as e:
                logger.debug(f"Failed to get GPU info: {e}")

        return snapshot

    def get_status(self, snapshot: ResourceSnapshot) -> ResourceStatus:
        """Get resource status based on snapshot."""
        if snapshot.memory_percent >= self.memory_critical_threshold * 100:
            return ResourceStatus.CRITICAL
        if snapshot.cpu_percent >= self.cpu_critical_threshold * 100:
            return ResourceStatus.CRITICAL
        if snapshot.memory_percent >= self.memory_warning_threshold * 100:
            return ResourceStatus.WARNING
        if snapshot.cpu_percent >= self.cpu_warning_threshold * 100:
            return ResourceStatus.WARNING
        return ResourceStatus.NORMAL

    def calculate_optimal_concurrency(self, snapshot: ResourceSnapshot) -> int:
        """Calculate optimal concurrent task count based on available resources."""
        # If we don't have resource info, use conservative default
        if snapshot.memory_percent == 0 and snapshot.cpu_percent == 0:
            return self._max_concurrent_tasks // 2

        memory_headroom = 1.0 - (snapshot.memory_percent / 100)
        cpu_headroom = 1.0 - snapshot.cpu_percent

        # Base concurrency on memory
        memory_based = int(memory_headroom * self._max_concurrent_tasks * 0.5)
        # Factor in CPU
        cpu_based = int(cpu_headroom * self._max_concurrent_tasks * 0.5)

        # Reserve slots for system
        optimal = max(1, min(memory_based + cpu_based, self._max_concurrent_tasks - 2))

        # For GPU-intensive tasks, reduce concurrency
        if snapshot.gpu_utilization > 50:
            optimal = min(optimal, 3)

        return optimal

    async def can_accept_task(self) -> tuple[bool, str]:
        """Check if system can accept new task."""
        snapshot = await self.get_snapshot()
        status = self.get_status(snapshot)

        if status == ResourceStatus.CRITICAL:
            return False, f"System critical: memory={snapshot.memory_percent:.1f}%, cpu={snapshot.cpu_percent:.1f}%"

        if self._current_tasks >= self.calculate_optimal_concurrency(snapshot):
            return False, f"Too many active tasks: {self._current_tasks}"

        return True, "OK"

    async def task_started(self) -> None:
        """Notify that a task has started."""
        async with self._lock:
            self._current_tasks += 1
            logger.debug(f"Task started, active: {self._current_tasks}")

    async def task_completed(self) -> None:
        """Notify that a task has completed."""
        async with self._lock:
            self._current_tasks = max(0, self._current_tasks - 1)
            logger.debug(f"Task completed, active: {self._current_tasks}")

    async def task_failed(self) -> None:
        """Notify that a task has failed."""
        await self.task_completed()


class CircuitBreaker:
    """Circuit breaker for handling cascading failures."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max_calls: int = 3,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls

        self._failure_count = 0
        self._last_failure_time = None
        self._state = "closed"  # closed, open, half_open
        self._half_open_calls = 0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> str:
        """Get current circuit breaker state."""
        return self._state

    async def can_execute(self) -> bool:
        """Check if execution is allowed."""
        async with self._lock:
            if self._state == "closed":
                return True

            if self._state == "open":
                # Check if recovery timeout has passed
                if self._last_failure_time:
                    elapsed = (datetime.utcnow() - self._last_failure_time).total_seconds()
                    if elapsed >= self.recovery_timeout:
                        self._state = "half_open"
                        self._half_open_calls = 0
                        logger.info("Circuit breaker entering half-open state")
                        return True
                return False

            if self._state == "half_open":
                return self._half_open_calls < self.half_open_max_calls

            return False

    async def record_success(self) -> None:
        """Record successful execution."""
        async with self._lock:
            if self._state == "half_open":
                self._half_open_calls += 1
                if self._half_open_calls >= self.half_open_max_calls:
                    self._state = "closed"
                    self._failure_count = 0
                    logger.info("Circuit breaker closed after successful recovery")
            else:
                self._failure_count = 0

    async def record_failure(self) -> None:
        """Record failed execution."""
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = datetime.utcnow()

            if self._state == "half_open":
                self._state = "open"
                logger.warning("Circuit breaker reopened after failure in half-open state")
            elif self._failure_count >= self.failure_threshold:
                self._state = "open"
                logger.warning(f"Circuit breaker opened after {self._failure_count} failures")
