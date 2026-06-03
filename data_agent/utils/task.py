# Copyright (c) Data Agent Team. All rights reserved.
"""Task-related dataclasses."""

from dataclasses import dataclass, field
from typing import Any, Optional
from enum import Enum
import uuid


class TaskState(Enum):
    """Task execution state."""

    PENDING = "pending"
    DECOMPOSING = "decomposing"
    PARSING = "parsing"
    PROCESSING = "processing"
    VALIDATING = "validating"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"


@dataclass
class SubTask:
    """Represents a single subtask in a decomposed workflow."""

    subtask_id: str
    agent_name: str
    skill_requirements: list[str]
    input_data: Any
    dependencies: list[str] = field(default_factory=list)
    retry_config: dict = field(default_factory=dict)
    status: str = "pending"

    def __post_init__(self):
        if not self.subtask_id:
            self.subtask_id = f"task_{uuid.uuid4().hex[:8]}"


@dataclass
class TaskDefinition:
    """Complete task definition with all metadata."""

    task_id: str
    instruction: str
    input_files: list[str]
    output_format: str = "middle_json"
    constraints: dict = field(default_factory=dict)
    subtasks: list[SubTask] = field(default_factory=list)

    def __post_init__(self):
        if not self.task_id:
            self.task_id = str(uuid.uuid4())


@dataclass
class TaskResult:
    """Result of task execution."""

    task_id: str
    status: TaskState
    output: Any = None
    error: Optional[str] = None
    subtask_results: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
