# Copyright (c) Data Agent Team. All rights reserved.
"""Agent base classes and core types."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional
import uuid

from loguru import logger


class AgentStatus(Enum):
    """Agent execution status."""

    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class AgentContext:
    """Context passed between agents during execution."""

    task_id: str
    original_input: Any
    current_state: dict = field(default_factory=dict)
    results: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    timeline_logger: Any = None  # TimelineLogger instance for structured logging

    def __post_init__(self):
        if not self.task_id:
            self.task_id = str(uuid.uuid4())


@dataclass
class AgentResponse:
    """Response returned by agent execution."""

    success: bool
    status: AgentStatus
    output: Any = None
    error: Optional[str] = None
    context: Optional[AgentContext] = None


class BaseAgent(ABC):
    """Abstract base class for all agents."""

    def __init__(self, name: str, config: dict = None):
        self.name = name
        self.config = config or {}
        self.status = AgentStatus.IDLE

    @abstractmethod
    async def execute(self, context: AgentContext) -> AgentResponse:
        """Execute the agent's task."""
        pass

    async def pre_execute(self, context: AgentContext) -> None:
        """Hook called before execution."""
        self.status = AgentStatus.RUNNING
        logger.info(f"[{self.name}] Starting execution for task {context.task_id}")

    async def post_execute(
        self, context: AgentContext, response: AgentResponse
    ) -> None:
        """Hook called after execution."""
        self.status = (
            AgentStatus.COMPLETED if response.success else AgentStatus.FAILED
        )
        logger.info(f"[{self.name}] Completed with status: {response.status}")

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(name={self.name})>"
