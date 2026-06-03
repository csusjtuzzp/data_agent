# Copyright (c) Data Agent Team. All rights reserved.
"""Storage implementations for task state and results."""

import json
import asyncio
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any, Optional

from loguru import logger


class MemoryStorage:
    """In-memory storage for task state."""

    def __init__(self, ttl_hours: int = 24):
        self._storage: dict[str, dict] = {}
        self._lock = asyncio.Lock()
        self._ttl_hours = ttl_hours

    async def set(self, task_id: str, data: dict) -> None:
        """Store task data."""
        async with self._lock:
            self._storage[task_id] = {
                "data": data,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }

    async def get(self, task_id: str) -> Optional[dict]:
        """Retrieve task data."""
        async with self._lock:
            entry = self._storage.get(task_id)
            if not entry:
                return None

            age = datetime.utcnow() - entry["created_at"]
            if age > timedelta(hours=self._ttl_hours):
                del self._storage[task_id]
                return None

            return entry["data"]

    async def delete(self, task_id: str) -> None:
        """Delete task data."""
        async with self._lock:
            self._storage.pop(task_id, None)

    async def list_tasks(self) -> list[str]:
        """List all task IDs."""
        async with self._lock:
            return list(self._storage.keys())

    async def cleanup_expired(self) -> int:
        """Remove expired entries. Returns count of removed entries."""
        async with self._lock:
            now = datetime.utcnow()
            expired = [
                task_id
                for task_id, entry in self._storage.items()
                if now - entry["created_at"] > timedelta(hours=self._ttl_hours)
            ]
            for task_id in expired:
                del self._storage[task_id]
            return len(expired)


class FileStorage:
    """File-based storage for task results."""

    def __init__(self, storage_dir: str = "./task_storage"):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    async def save(self, task_id: str, data: dict) -> None:
        """Save task data to file."""
        async with self._lock:
            file_path = self._get_path(task_id)
            with open(file_path, "w") as f:
                json.dump(data, f, indent=2, default=str)

    async def load(self, task_id: str) -> Optional[dict]:
        """Load task data from file."""
        file_path = self._get_path(task_id)
        if not file_path.exists():
            return None

        with open(file_path, "r") as f:
            return json.load(f)

    async def delete(self, task_id: str) -> None:
        """Delete task data file."""
        async with self._lock:
            file_path = self._get_path(task_id)
            if file_path.exists():
                file_path.unlink()

    def _get_path(self, task_id: str) -> Path:
        """Get file path for task."""
        safe_id = task_id.replace("/", "_").replace("\\", "_")
        return self.storage_dir / f"{safe_id}.json"
