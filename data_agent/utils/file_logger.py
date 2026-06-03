# Copyright (c) Data Agent Team. All rights reserved.
"""File Logger - 目录结构管理和文件写入"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


class FileLogger:
    """可复现性文件日志管理器

    目录结构:
        logs/
        └── {task_id}-{filename}/
            ├── request.json
            ├── execution_trace.jsonl
            ├── planner.json
            ├── reflection.json
            ├── final_output.json
            ├── metrics.json
            ├── pages/
            │   └── p{n}/
            │       ├── parse.json
            │       ├── middle.json
            │       ├── reflection.json
            │       └── graph.json
            ├── artifacts/
            │   ├── rendered_html/
            │   ├── markdown/
            │   ├── tables/
            │   └── figures/
            └── errors/
                └── retry.log
    """

    def __init__(self, task_id: str, filename: str, base_dir: str = "logs"):
        self.task_id = task_id
        self.filename = filename
        self.base_dir = Path(base_dir)
        # 清理 filename 中的特殊字符
        safe_filename = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)
        # 目录名: {task_id}-{filename}
        self._task_dir = self.base_dir / f"{task_id}-{safe_filename}"
        self._task_dir.mkdir(parents=True, exist_ok=True)

    @property
    def task_dir(self) -> Path:
        """获取任务目录"""
        return self._task_dir

    def create_subdirs(self):
        """创建所有子目录"""
        subdirs = [
            "pages",
            "artifacts/rendered_html",
            "artifacts/markdown",
            "artifacts/tables",
            "artifacts/figures",
            "errors",
        ]
        for subdir in subdirs:
            (self._task_dir / subdir).mkdir(parents=True, exist_ok=True)

    def write_json(self, filename: str, data: dict):
        """写入 JSON 文件"""
        filepath = self._task_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        return str(filepath)

    def write_jsonl(self, filename: str, logs: list):
        """写入 JSON Lines 文件"""
        filepath = self._task_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            for log in logs:
                f.write(json.dumps(log, ensure_ascii=False, default=str) + "\n")
        return str(filepath)

    def append_jsonl(self, filename: str, log_entry: dict):
        """追加 JSON Lines 条目"""
        filepath = self._task_dir / filename
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False, default=str) + "\n")

    def write_page_json(self, page_num: int, filename: str, data: dict):
        """写入页面级 JSON 文件"""
        page_dir = self._task_dir / "pages" / f"p{page_num}"
        page_dir.mkdir(parents=True, exist_ok=True)
        filepath = page_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        return str(filepath)

    def write_error_log(self, message: str, context: dict = None):
        """写入错误日志"""
        filepath = self._task_dir / "errors" / "retry.log"
        timestamp = datetime.utcnow().isoformat()
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")
            if context:
                f.write(f"  Context: {json.dumps(context, ensure_ascii=False, default=str)}\n")

    def write_request(self, request_data: dict):
        """写入 request.json"""
        # 添加 filename 字段到请求数据
        request_data["filename"] = self.filename
        request_data["task_id"] = self.task_id
        return self.write_json("request.json", request_data)

    def write_final_output(self, output_data: dict):
        """写入 final_output.json"""
        return self.write_json("final_output.json", output_data)

    def write_metrics(self, metrics_data: dict):
        """写入 metrics.json"""
        return self.write_json("metrics.json", metrics_data)

    def write_planner(self, planner_data: dict):
        """追加 planner 数据到 planner.jsonl"""
        return self.append_jsonl("planner.jsonl", planner_data)

    def write_reflection(self, reflection_data: dict):
        """追加 reflection 数据到 reflection.jsonl"""
        return self.append_jsonl("reflection.jsonl", reflection_data)

    def write_execution_trace(self, trace_logs: list):
        """写入 execution_trace.jsonl"""
        return self.write_jsonl("execution_trace.jsonl", trace_logs)

    def get_task_dir(self) -> str:
        """获取任务目录路径"""
        return str(self._task_dir)