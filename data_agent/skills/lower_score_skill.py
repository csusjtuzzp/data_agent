# Copyright (c) Data Agent Team. All rights reserved.
"""LowerScoreSkill - 降低问题页面的评分但保留内容"""

from typing import Any

from loguru import logger

from data_agent.skills.base_skill import BaseSkill, SkillConfig


class LowerScoreSkill(BaseSkill):
    """降低问题页面的评分但保留内容"""

    def __init__(self, config: SkillConfig):
        super().__init__(config)
        self.default_score = config.parameters.get("default_score", 0.5)
        self.critical_score = config.parameters.get("critical_score", 0.3)

    async def execute(self, middle_json: dict, issues: list = None, **kwargs) -> dict:
        """降低问题页面的评分但保留内容"""
        if not self.validate_input(middle_json):
            raise ValueError("Invalid middle_json input")

        if not issues:
            logger.info("[LowerScore] No issues provided, returning as-is")
            return middle_json

        # 收集问题页面
        problem_pages = set()
        critical_pages = set()

        for issue in issues:
            page_idx = issue.get("page", -1)
            if page_idx < 0:
                continue

            problem_pages.add(page_idx)
            if issue.get("level") == "critical":
                critical_pages.add(page_idx)

        if not problem_pages:
            logger.info("[LowerScore] No problem pages found")
            return middle_json

        logger.info(
            f"[LowerScore] Marking {len(problem_pages)} pages with issues "
            f"({len(critical_pages)} critical)"
        )

        # 标记问题页面
        for page in middle_json.get("pdf_info", []):
            page_idx = page.get("page_idx")
            if page_idx in critical_pages:
                page["_quality_score"] = self.critical_score
                page["_has_critical_issues"] = True
            elif page_idx in problem_pages:
                page["_quality_score"] = self.default_score
                page["_has_issues"] = True

        # 记录问题类型
        issue_types_per_page = {}
        for issue in issues:
            page_idx = issue.get("page", -1)
            if page_idx < 0:
                continue
            if page_idx not in issue_types_per_page:
                issue_types_per_page[page_idx] = []
            issue_types_per_page[page_idx].append(issue.get("type"))

        middle_json["_page_issues"] = issue_types_per_page
        middle_json["_total_problem_pages"] = len(problem_pages)
        middle_json["_total_critical_pages"] = len(critical_pages)

        logger.info(f"[LowerScore] Finished marking {len(problem_pages)} pages")
        return middle_json

    def validate_input(self, data: Any) -> bool:
        """验证 middle_json 结构"""
        return isinstance(data, dict) and "pdf_info" in data