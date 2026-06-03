# Copyright (c) Data Agent Team. All rights reserved.
"""SwitchBackendSkill - 根据问题类型决定切换到哪个后端"""

from collections import Counter
from typing import Any, Optional

from loguru import logger

from data_agent.skills.base_skill import BaseSkill, SkillConfig


# 后端优先级链
BACKEND_CHAIN = ["pipeline", "hybrid", "vlm-auto-engine"]

# 问题类型到后端的映射
ISSUE_TO_BACKEND = {
    "table_structure_invalid": "hybrid",
    "low_text_content": "pipeline",
    "insufficient_blocks": "pipeline",
    "high_empty_ratio": None,  # 需要根据当前后端决定
    "empty_page": None,
    "high_discarded_ratio": None,
}


class SwitchBackendSkill(BaseSkill):
    """根据问题类型决定切换到哪个后端"""

    def __init__(self, config: SkillConfig):
        super().__init__(config)
        self.default_backend = config.parameters.get("default_backend", "pipeline")

    async def execute(
        self,
        parse_result: dict = None,
        issues: list = None,
        current_backend: str = None,
        **kwargs
    ) -> dict:
        """决定是否需要切换后端以及切换到哪个后端"""
        if not issues:
            logger.info("[SwitchBackend] No issues provided, no switch needed")
            return {
                "need_retry": False,
                "current_backend": current_backend,
                "next_backend": None,
                "reason": None,
            }

        # 分析问题类型
        issue_types = Counter(i.get("type") for i in issues)
        problem_pages = set(i.get("page") for i in issues if i.get("page", -1) >= 0)

        logger.info(f"[SwitchBackend] Issue types: {dict(issue_types)}")
        logger.info(f"[SwitchBackend] Problem pages: {problem_pages}")

        # 判断是否需要切换
        next_backend = self._decide_backend(current_backend, issue_types, problem_pages)

        if next_backend is None:
            logger.info("[SwitchBackend] No backend switch needed")
            return {
                "need_retry": False,
                "current_backend": current_backend,
                "next_backend": None,
                "reason": "single_page_issue",
            }

        logger.info(f"[SwitchBackend] Switching from {current_backend} to {next_backend}")

        return {
            "need_retry": True,
            "current_backend": current_backend,
            "next_backend": next_backend,
            "reason": self._get_switch_reason(issue_types),
            "original_result": parse_result,
        }

    def _decide_backend(
        self,
        current_backend: Optional[str],
        issue_types: Counter,
        problem_pages: set
    ) -> Optional[str]:
        """根据问题类型和当前后端决定下一个后端"""
        # 单一页面问题通常不需要切换
        if len(problem_pages) <= 1:
            return None

        # 根据问题类型直接决定
        for issue_type, target_backend in ISSUE_TO_BACKEND.items():
            if issue_type in issue_types:
                if target_backend:
                    return target_backend

        # 需要根据当前后端决定的问题类型
        if "high_empty_ratio" in issue_types or "empty_page" in issue_types:
            return self._chain_backend(current_backend)

        if "high_discarded_ratio" in issue_types:
            return self._chain_backend(current_backend)

        # 默认按优先级链切换
        return self._chain_backend(current_backend)

    def _chain_backend(self, current_backend: Optional[str]) -> str:
        """按优先级链切换后端"""
        if not current_backend:
            return self.default_backend

        # 找到当前后端的位置
        try:
            current_idx = BACKEND_CHAIN.index(current_backend)
        except ValueError:
            return self.default_backend

        # 切换到下一个后端
        next_idx = (current_idx + 1) % len(BACKEND_CHAIN)
        return BACKEND_CHAIN[next_idx]

    def _get_switch_reason(self, issue_types: Counter) -> str:
        """获取切换原因"""
        primary_issue = issue_types.most_common(1)[0][0] if issue_types else "unknown"
        reasons = {
            "table_structure_invalid": "table_structure_issues",
            "low_text_content": "low_text_content",
            "high_empty_ratio": "high_empty_ratio",
            "empty_page": "empty_pages",
            "high_discarded_ratio": "high_discarded_ratio",
        }
        return reasons.get(primary_issue, "quality_issues")

    def validate_input(self, data: Any) -> bool:
        """验证输入"""
        return data is None or isinstance(data, dict) or isinstance(data, list)