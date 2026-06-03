# Copyright (c) Data Agent Team. All rights reserved.
"""AbortTaskSkill - 文件损坏时终止任务"""

from typing import Any

from loguru import logger

from data_agent.skills.base_skill import BaseSkill, SkillConfig


class AbortTaskSkill(BaseSkill):
    """文件损坏时终止任务"""

    def __init__(self, config: SkillConfig):
        super().__init__(config)
        self.abort_on_insufficient_pages = config.parameters.get(
            "abort_on_insufficient_pages", True
        )
        self.abort_on_corruption = config.parameters.get(
            "abort_on_corruption", True
        )

    async def execute(self, parse_result: dict = None, issues: list = None, **kwargs) -> dict:
        """检查是否需要终止任务"""
        if not issues:
            logger.info("[AbortTask] No issues provided, not aborting")
            return parse_result or {}

        # 检查是否有致命问题
        critical_issues = [i for i in issues if i.get("level") == "critical"]

        should_abort = False
        abort_reasons = []

        for issue in critical_issues:
            issue_type = issue.get("type")

            if issue_type == "insufficient_pages" and self.abort_on_insufficient_pages:
                should_abort = True
                abort_reasons.append("insufficient_pages")

            if issue_type == "file_corrupted" and self.abort_on_corruption:
                should_abort = True
                abort_reasons.append("file_corrupted")

            if issue_type == "parse_failed":
                should_abort = True
                abort_reasons.append("parse_failed")

        if should_abort:
            logger.warning(f"[AbortTask] Aborting task due to: {abort_reasons}")
            return {
                "abort": True,
                "abort_reasons": abort_reasons,
                "original_result": parse_result,
                "error_message": f"Task aborted due to: {', '.join(abort_reasons)}",
            }

        logger.info("[AbortTask] No abort conditions met, continuing")
        return parse_result or {}

    def validate_input(self, data: Any) -> bool:
        """验证输入"""
        return data is None or isinstance(data, dict)