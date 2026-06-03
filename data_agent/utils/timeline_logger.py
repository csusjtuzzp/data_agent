# Copyright (c) Data Agent Team. All rights reserved.
"""Timeline Logger - 高可追溯性结构化日志"""

import inspect
import os
import sys
from datetime import datetime
from enum import Enum
from typing import Optional
from loguru import logger


class LogStatus(Enum):
    """日志状态/颜色对应"""
    SUCCESS = "success"   # green
    RUNNING = "running"   # blue
    WARNING = "warning"   # yellow
    ERROR = "error"       # red


class TimelineLogger:
    """Timeline 格式日志记录器

    格式: [时间][代码文件:行数][hash[:16]-文件名][agent名称][sub-agent名称][skill名称]输出内容
    """

    def __init__(self, task_id: str, filename: str):
        self.task_id = task_id
        self.filename = os.path.basename(filename) if filename else "unknown"
        self._logs = []

    def _get_caller_info(self) -> str:
        """获取调用者文件和行号"""
        for frame_info in inspect.stack()[2:]:
            fname = frame_info.filename
            if "timeline_logger.py" not in fname:
                basename = os.path.basename(fname)
                lineno = frame_info.lineno
                return f"{basename}:{lineno}"
        return "unknown:0"

    def _format_time(self) -> str:
        """格式化时间 HH:MM:SS"""
        return datetime.utcnow().strftime("%H:%M:%S")

    def _format_id(self) -> str:
        """格式化 filename (已经是 SHA256 格式: hash[:16]-original_filename)"""
        return self.filename

    def suppress_other_logs(self):
        """抑制其他日志输出，只保留 Timeline 格式"""
        logger.remove()

    def restore_logs(self):
        """恢复其他日志输出"""
        # 不恢复，让外部统一管理

    def log(
        self,
        agent_name: str,
        level: str,
        action: str,
        sub_agent: str = "",
        skill: str = "",
        status: LogStatus = LogStatus.RUNNING,
    ):
        """记录一条 timeline 日志

        Args:
            agent_name: Agent 名称
            level: 日志级别 (debug/info/warning/error)
            action: 输出内容
            sub_agent: 子 Agent 名称
            skill: Skill 名称
            status: 状态 (success/running/warning/error)
        """
        caller_info = self._get_caller_info()
        timestamp = self._format_time()
        task_id_file = self._format_id()

        # 格式化各部分
        sub_agent_str = f"[{sub_agent}]" if sub_agent else ""
        skill_str = f"[{skill}]" if skill else ""

        log_line = f"[{timestamp}][{caller_info}][{task_id_file}][{agent_name}]{sub_agent_str}{skill_str} {action}"

        # 颜色ANSI码 (用于终端显示)
        color_map = {
            LogStatus.SUCCESS: "\033[32m",   # green
            LogStatus.RUNNING: "\033[34m",   # blue
            LogStatus.WARNING: "\033[33m",  # yellow
            LogStatus.ERROR: "\033[31m",     # red
        }
        reset = "\033[0m"
        color = color_map.get(status, "\033[34m")

        # 直接 print 输出，不走 loguru，避免格式前缀
        print(f"{color}{log_line}{reset}")

        # 存储结构化日志
        self._logs.append({
            "timestamp": datetime.utcnow().isoformat(),
            "task_id": self.task_id,
            "filename": self.filename,
            "agent": agent_name,
            "sub_agent": sub_agent,
            "skill": skill,
            "caller": caller_info,
            "action": action,
            "status": status.value,
            "level": level,
        })

    def success(self, agent_name: str, action: str, sub_agent: str = "", skill: str = ""):
        """记录成功日志"""
        self.log(agent_name, "info", action, sub_agent, skill, LogStatus.SUCCESS)

    def running(self, agent_name: str, action: str, sub_agent: str = "", skill: str = ""):
        """记录运行中日志"""
        self.log(agent_name, "info", action, sub_agent, skill, LogStatus.RUNNING)

    def warning(self, agent_name: str, action: str, sub_agent: str = "", skill: str = ""):
        """记录警告日志"""
        self.log(agent_name, "warning", action, sub_agent, skill, LogStatus.WARNING)

    def error(self, agent_name: str, action: str, sub_agent: str = "", skill: str = ""):
        """记录错误日志"""
        self.log(agent_name, "error", action, sub_agent, skill, LogStatus.ERROR)

    def get_logs(self) -> list[dict]:
        """获取所有日志"""
        return self._logs

    def get_logs_text(self) -> str:
        """获取日志的文本格式（与终端输出一致）"""
        lines = []
        for log in self._logs:
            timestamp = datetime.fromisoformat(log["timestamp"]).strftime("%H:%M:%S")
            caller = log["caller"]
            filename = log["filename"]
            agent = log["agent"]
            sub_agent = log.get("sub_agent", "")
            skill = log.get("skill", "")
            action = log["action"]
            status = log["status"]

            sub_agent_str = f"[{sub_agent}]" if sub_agent else ""
            skill_str = f"[{skill}]" if skill else ""
            log_line = f"[{timestamp}][{caller}][{filename}][{agent}]{sub_agent_str}{skill_str} {action}"
            lines.append(log_line)
        return "\n".join(lines)

    def to_jsonl(self) -> list[str]:
        """转换为 JSON Lines 格式"""
        import json
        return [json.dumps(log, ensure_ascii=False, default=str) for log in self._logs]