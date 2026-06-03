# Copyright (c) Data Agent Team. All rights reserved.
"""Persistent task storage using SQLite."""

import asyncio
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from loguru import logger


class PersistentTaskStorage:
    """SQLite-based persistent task storage."""

    def __init__(self, db_path: str = "./data_agent_tasks.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    instruction TEXT NOT NULL,
                    input_data TEXT,
                    status TEXT NOT NULL,
                    progress REAL DEFAULT 0.0,
                    result TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    metadata TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_steps (
                    step_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    agent_name TEXT,
                    action TEXT,
                    status TEXT NOT NULL,
                    output TEXT,
                    error TEXT,
                    start_time TEXT,
                    end_time TEXT,
                    execution_time_ms REAL,
                    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at)
            """)
            conn.commit()

    @contextmanager
    def _get_connection(self):
        """Get database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    async def create_task(
        self,
        task_id: str,
        instruction: str,
        input_data: Any,
        metadata: dict = None,
    ) -> None:
        """Create a new task."""
        async with self._lock:
            now = datetime.utcnow().isoformat()
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO tasks (task_id, instruction, input_data, status, created_at, updated_at, metadata)
                    VALUES (?, ?, ?, 'pending', ?, ?, ?)
                    """,
                    (task_id, instruction, json.dumps(input_data), now, now, json.dumps(metadata or {})),
                )
                conn.commit()

    async def get_task(self, task_id: str) -> Optional[dict]:
        """Get task by ID."""
        async with self._lock:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
                ).fetchone()

                if not row:
                    return None

                return self._row_to_dict(row)

    async def update_task(
        self,
        task_id: str,
        status: str = None,
        progress: float = None,
        result: Any = None,
        error: str = None,
    ) -> None:
        """Update task status and result."""
        async with self._lock:
            now = datetime.utcnow().isoformat()
            updates = ["updated_at = ?"]
            values = [now]

            if status:
                updates.append("status = ?")
                values.append(status)
                if status == "completed":
                    updates.append("completed_at = ?")
                    values.append(now)

            if progress is not None:
                updates.append("progress = ?")
                values.append(progress)

            if result is not None:
                updates.append("result = ?")
                values.append(json.dumps(result))

            if error:
                updates.append("error = ?")
                values.append(error)

            values.append(task_id)

            with self._get_connection() as conn:
                conn.execute(
                    f"UPDATE tasks SET {', '.join(updates)} WHERE task_id = ?",
                    values,
                )
                conn.commit()

    async def list_tasks(
        self,
        status: str = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """List tasks with optional status filter."""
        async with self._lock:
            with self._get_connection() as conn:
                if status:
                    rows = conn.execute(
                        """
                        SELECT * FROM tasks WHERE status = ?
                        ORDER BY created_at DESC LIMIT ? OFFSET ?
                        """,
                        (status, limit, offset),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT * FROM tasks
                        ORDER BY created_at DESC LIMIT ? OFFSET ?
                        """,
                        (limit, offset),
                    ).fetchall()

                return [self._row_to_dict(row) for row in rows]

    async def delete_task(self, task_id: str) -> None:
        """Delete a task and its steps."""
        async with self._lock:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM task_steps WHERE task_id = ?", (task_id,))
                conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
                conn.commit()

    async def save_step(
        self,
        step_id: str,
        task_id: str,
        agent_name: str,
        action: str,
        status: str,
        output: Any = None,
        error: str = None,
        start_time: datetime = None,
        end_time: datetime = None,
        execution_time_ms: float = None,
    ) -> None:
        """Save task step execution record."""
        async with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO task_steps
                    (step_id, task_id, agent_name, action, status, output, error, start_time, end_time, execution_time_ms)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        step_id,
                        task_id,
                        agent_name,
                        action,
                        status,
                        json.dumps(output) if output else None,
                        error,
                        start_time.isoformat() if start_time else None,
                        end_time.isoformat() if end_time else None,
                        execution_time_ms,
                    ),
                )
                conn.commit()

    async def get_task_steps(self, task_id: str) -> list[dict]:
        """Get all steps for a task."""
        async with self._lock:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT * FROM task_steps WHERE task_id = ? ORDER BY start_time",
                    (task_id,),
                ).fetchall()

                return [self._row_to_dict(row) for row in rows]

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        """Convert row to dictionary."""
        result = dict(row)

        # Parse JSON fields
        for field in ['input_data', 'result', 'metadata']:
            if field in result and result[field]:
                try:
                    result[field] = json.loads(result[field])
                except (json.JSONDecodeError, TypeError):
                    pass

        return result

    async def cleanup_old_tasks(self, days: int = 7) -> int:
        """Delete tasks older than specified days. Returns count of deleted tasks."""
        async with self._lock:
            cutoff = datetime.utcnow()
            cutoff = cutoff.replace(day=cutoff.day - days)

            with self._get_connection() as conn:
                cursor = conn.execute(
                    "DELETE FROM tasks WHERE created_at < ? AND status IN ('completed', 'failed')",
                    (cutoff.isoformat(),),
                )
                conn.commit()
                return cursor.rowcount
