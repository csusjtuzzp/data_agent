# Copyright (c) Data Agent Team. All rights reserved.
"""Task storage for managing task state."""

import asyncio
from datetime import datetime
from typing import Any, Optional


class TaskStorage:
    """In-memory task state storage."""

    def __init__(self):
        self._task_info: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    def create_task(self, task_id: str, file_path: Optional[str] = None) -> None:
        """Create new task entry."""
        self._task_info[task_id] = {
            "task_id": task_id,
            "status": "pending",
            "progress": 0.0,
            "current_step": None,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
            "result": None,
            "error": None,
            "file_path": file_path,
        }

    def get_task(self, task_id: str) -> Optional[dict]:
        """Get task information."""
        return self._task_info.get(task_id)

    def update_status(
        self,
        task_id: str,
        status: str,
        progress: Optional[float] = None,
        current_step: Optional[str] = None,
        result: Any = None,
        error: Optional[str] = None,
        output_dir: Optional[str] = None,
    ) -> None:
        """Update task status."""
        if task_id not in self._task_info:
            return

        task_info = self._task_info[task_id]
        task_info["status"] = status
        task_info["updated_at"] = datetime.utcnow()

        if progress is not None:
            task_info["progress"] = progress
        if current_step:
            task_info["current_step"] = current_step
        if result is not None:
            task_info["result"] = result
        if error:
            task_info["error"] = error
        if output_dir is not None:
            task_info["output_dir"] = output_dir
