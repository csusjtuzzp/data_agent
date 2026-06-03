# Copyright (c) Data Agent Team. All rights reserved.
"""RouteByIssueSkill - 根据问题类型路由到对应的处理 Skill"""

from typing import Any

from loguru import logger

from data_agent.skills.base_skill import BaseSkill, SkillConfig


# 问题类型 -> 处理动作 映射
ISSUE_TYPE_ACTIONS = {
    "empty_page": "remove_empty_pages",
    "high_empty_ratio": "remove_empty_pages",
    "low_text_content": "lower_score",
    "table_structure_invalid": "repair_table",
    "insufficient_pages": "abort_task",
    "insufficient_blocks": "lower_score",
    "high_discarded_ratio": "switch_backend",
    "missing_backend": "continue",
}


class RouteByIssueSkill(BaseSkill):
    """根据问题类型路由到对应的处理 Skill"""

    def __init__(self, config: SkillConfig):
        super().__init__(config)
        self.action_mapping = config.parameters.get(
            "action_mapping", ISSUE_TYPE_ACTIONS
        )

    async def execute(self, validation_result: dict = None, **kwargs) -> dict:
        """分析验证结果并决定处理动作"""
        if not validation_result:
            logger.info("[RouteByIssue] No validation result, continuing")
            return {"action": "continue"}

        issues = validation_result.get("issues", [])
        if not issues:
            logger.info("[RouteByIssue] No issues found, continuing")
            return {"action": "continue"}

        # 分析问题
        issue_analysis = self._analyze_issues(issues)

        # 决定动作
        action = self._decide_action(issue_analysis)

        logger.info(f"[RouteByIssue] Decided action: {action}")

        return {
            "action": action,
            "issue_analysis": issue_analysis,
            "issues": issues,
        }

    def validate_input(self, data: Any) -> bool:
        """验证输入"""
        return data is None or isinstance(data, dict)

    def _analyze_issues(self, issues: list) -> dict:
        """分析问题类型和严重程度"""
        analysis = {
            "total_issues": len(issues),
            "critical_count": 0,
            "warning_count": 0,
            "issue_types": {},
            "problem_pages": set(),
            "page_issue_map": {},
            "needs_switch_backend": False,
            "needs_abort": False,
        }

        for issue in issues:
            issue_type = issue.get("type", "unknown")
            level = issue.get("level", "warning")
            page_idx = issue.get("page", -1)

            # 统计级别
            if level == "critical":
                analysis["critical_count"] += 1
            else:
                analysis["warning_count"] += 1

            # 统计类型
            if issue_type not in analysis["issue_types"]:
                analysis["issue_types"][issue_type] = []
            analysis["issue_types"][issue_type].append(issue)

            # 记录问题页面
            if page_idx >= 0:
                analysis["problem_pages"].add(page_idx)
                if page_idx not in analysis["page_issue_map"]:
                    analysis["page_issue_map"][page_idx] = []
                analysis["page_issue_map"][page_idx].append(issue_type)

            # 检查是否需要切换后端
            if issue_type in ["high_discarded_ratio", "table_structure_invalid"]:
                analysis["needs_switch_backend"] = True

            # 检查是否需要终止
            if issue_type == "insufficient_pages":
                analysis["needs_abort"] = True

        return analysis

    def _decide_action(self, analysis: dict) -> str:
        """根据分析结果决定动作"""
        # 最高优先级：终止任务
        if analysis["needs_abort"]:
            return "abort_task"

        # 检查问题页面数量
        total_pages = len(analysis["problem_pages"])

        # 单一页面问题
        if total_pages == 1:
            page_issues = list(analysis["page_issue_map"].values())[0]
            if "table_structure_invalid" in page_issues:
                return "repair_table"
            elif "empty_page" in page_issues or "high_empty_ratio" in page_issues:
                return "remove_empty_pages"
            elif "low_text_content" in page_issues or "insufficient_blocks" in page_issues:
                return "lower_score"
            return "continue"

        # 多页面问题
        if analysis["needs_switch_backend"]:
            return "switch_backend"

        # 大量问题页面
        if total_pages > 5:
            return "switch_backend"

        # 多个警告
        if analysis["warning_count"] > 3:
            return "switch_backend"

        return "continue"


class RouteByIssueSkillV2(BaseSkill):
    """根据问题类型路由 - V2 版本支持组合动作"""

    def __init__(self, config: SkillConfig):
        super().__init__(config)

    async def execute(self, validation_result: dict = None, **kwargs) -> dict:
        """分析验证结果并返回处理计划"""
        if not validation_result:
            return {"action": "continue", "steps": []}

        issues = validation_result.get("issues", [])
        if not issues:
            return {"action": "continue", "steps": []}

        # 生成处理步骤
        steps = self._generate_steps(issues)

        return {
            "action": "composite" if len(steps) > 1 else (steps[0]["action"] if steps else "continue"),
            "steps": steps,
            "issue_count": len(issues),
        }

    def validate_input(self, data: Any) -> bool:
        return data is None or isinstance(data, dict)

    def _generate_steps(self, issues: list) -> list:
        """根据问题生成处理步骤"""
        steps = []
        seen_actions = set()

        # 按优先级排序问题类型
        priority_order = [
            "insufficient_pages",
            "table_structure_invalid",
            "empty_page",
            "low_text_content",
            "high_discarded_ratio",
            "insufficient_blocks",
        ]

        for issue_type in priority_order:
            matching_issues = [i for i in issues if i.get("type") == issue_type]
            if not matching_issues:
                continue

            action = ISSUE_TYPE_ACTIONS.get(issue_type, "continue")
            if action == "continue" or action in seen_actions:
                continue

            # 构建步骤
            step = {
                "action": action,
                "issue_type": issue_type,
                "count": len(matching_issues),
                "pages": [i.get("page", -1) for i in matching_issues if i.get("page", -1) >= 0],
            }

            steps.append(step)
            seen_actions.add(action)

            # 只保留第一个匹配的动作
            break

        return steps