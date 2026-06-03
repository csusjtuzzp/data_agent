# Copyright (c) Data Agent Team. All rights reserved.
"""RemoveEmptyPagesSkill - 移除空页，保留有效页面"""

from typing import Any

from loguru import logger

from data_agent.skills.base_skill import BaseSkill, SkillConfig


class RemoveEmptyPagesSkill(BaseSkill):
    """移除空页，保留有效页面"""

    def __init__(self, config: SkillConfig):
        super().__init__(config)
        self.min_block_count = config.parameters.get("min_block_count", 1)
        self.allowed_block_types = config.parameters.get(
            "allowed_block_types",
            ["text", "title", "table", "image", "chart", "list", "code"]
        )

    async def execute(self, middle_json: dict, issues: list = None, **kwargs) -> dict:
        """移除空页，保留有效页面"""
        if not self.validate_input(middle_json):
            raise ValueError("Invalid middle_json input")

        if not issues:
            logger.info("[RemoveEmptyPages] No issues provided, returning as-is")
            return middle_json

        # 收集空页页码
        empty_pages = set()
        for issue in issues:
            if issue.get("type") == "empty_page" and issue.get("page", -1) >= 0:
                empty_pages.add(issue["page"])

        if not empty_pages:
            logger.info("[RemoveEmptyPages] No empty pages to remove")
            return middle_json

        logger.info(f"[RemoveEmptyPages] Removing {len(empty_pages)} empty pages: {empty_pages}")

        # 过滤掉空页
        pages = middle_json.get("pdf_info", [])
        filtered_pages = [p for p in pages if p.get("page_idx") not in empty_pages]
        middle_json["pdf_info"] = filtered_pages

        # 更新元数据
        middle_json["_removed_pages"] = list(empty_pages)
        middle_json["_original_page_count"] = len(pages)
        middle_json["_filtered_page_count"] = len(filtered_pages)

        logger.info(f"[RemoveEmptyPages] Remaining pages: {len(filtered_pages)}")
        return middle_json

    def validate_input(self, data: Any) -> bool:
        """验证 middle_json 结构"""
        return isinstance(data, dict) and "pdf_info" in data

    def _is_empty_page(self, page: dict) -> bool:
        """判断页面是否为空"""
        blocks = page.get("preproc_blocks", [])

        # 没有 blocks 肯定是空页
        if not blocks:
            return True

        # 只包含 page_number 或 header 的页面也视为空页
        meaningful_blocks = [
            b for b in blocks
            if b.get("type") in self.allowed_block_types
        ]

        return len(meaningful_blocks) < self.min_block_count