# Copyright (c) Data Agent Team. All rights reserved.
"""RepairTableSkill - 修复表格结构问题"""

import re
from typing import Any

from loguru import logger

from data_agent.skills.base_skill import BaseSkill, SkillConfig


class RepairTableSkill(BaseSkill):
    """修复表格 HTML 结构问题"""

    def __init__(self, config: SkillConfig):
        super().__init__(config)
        self.min_rows = config.parameters.get("min_rows", 1)
        self.min_cols = config.parameters.get("min_cols", 1)
        self.repair_method = config.parameters.get("repair_method", "simplify")

    async def execute(self, middle_json: dict, issues: list = None, **kwargs) -> dict:
        """修复表格结构问题"""
        if not self.validate_input(middle_json):
            raise ValueError("Invalid middle_json input")

        if not issues:
            logger.info("[RepairTable] No issues provided, returning as-is")
            return middle_json

        # 找到结构问题的表格并修复
        table_issues = [
            i for i in issues
            if i.get("type") == "table_structure_invalid"
        ]

        if not table_issues:
            logger.info("[RepairTable] No table structure issues found")
            return middle_json

        logger.info(f"[RepairTable] Found {len(table_issues)} table structure issues")

        repaired_count = 0
        for issue in table_issues:
            page_idx = issue.get("page", -1)
            block_idx = issue.get("block_index", -1)

            middle_json = self._fix_table_html(middle_json, page_idx, block_idx)
            repaired_count += 1

        middle_json["_repaired_tables"] = repaired_count
        logger.info(f"[RepairTable] Repaired {repaired_count} tables")

        return middle_json

    def validate_input(self, data: Any) -> bool:
        """验证 middle_json 结构"""
        return isinstance(data, dict) and "pdf_info" in data

    def _fix_table_html(self, middle_json: dict, page_idx: int, block_idx: int) -> dict:
        """修复指定表格的 HTML 结构"""
        pages = middle_json.get("pdf_info", [])

        # 找到目标页面
        target_page = None
        for page in pages:
            if page.get("page_idx") == page_idx:
                target_page = page
                break

        if not target_page:
            logger.warning(f"[RepairTable] Page {page_idx} not found")
            return middle_json

        blocks = target_page.get("preproc_blocks", [])
        if block_idx >= len(blocks):
            logger.warning(f"[RepairTable] Block index {block_idx} out of range")
            return middle_json

        block = blocks[block_idx]
        if block.get("type") != "table":
            logger.warning(f"[RepairTable] Block {block_idx} is not a table")
            return middle_json

        # 获取当前 HTML
        html = block.get("html", "")

        # 根据修复方法选择策略
        if self.repair_method == "simplify":
            new_html = self._simplify_table(html)
        elif self.repair_method == "rebuild":
            new_html = self._rebuild_table(html)
        elif self.repair_method == "text_fallback":
            new_html = self._table_to_text(html)
        else:
            new_html = self._simplify_table(html)

        # 更新 block
        block["html"] = new_html
        block["_repaired"] = True
        block["_repair_method"] = self.repair_method

        logger.info(f"[RepairTable] Repaired table at page {page_idx}, block {block_idx}")
        return middle_json

    def _simplify_table(self, html: str) -> str:
        """简化表格结构，保留基本结构"""
        # 移除空的行和列
        lines = html.split("\n")
        cleaned_lines = []

        for line in lines:
            # 跳过空行
            stripped = line.strip()
            if not stripped:
                continue

            # 移除过多的空白
            cleaned = re.sub(r'\s+', ' ', stripped)
            cleaned_lines.append(cleaned)

        return "\n".join(cleaned_lines)

    def _rebuild_table(self, html: str) -> str:
        """重建表格结构"""
        # 尝试解析并重建表格
        text = re.sub(r'<[^>]+>', '', html)
        rows = text.split('\n')

        if len(rows) < 2:
            return self._table_to_text(html)

        # 构建简单的 HTML 表格
        table_lines = ['<table>']
        for row in rows:
            if row.strip():
                cells = row.split('\t')
                table_lines.append('<tr>')
                for cell in cells:
                    table_lines.append(f'<td>{cell.strip()}</td>')
                table_lines.append('</tr>')
        table_lines.append('</table>')

        return '\n'.join(table_lines)

    def _table_to_text(self, html: str) -> str:
        """将表格转换为文本格式"""
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'\s+', ' ', text)
        text = text.strip()

        # 包装成特殊格式
        return f"[TABLE_CONTENT]\n{text}\n[/TABLE_CONTENT]"


class RepairTableSkillV2(BaseSkill):
    """修复表格结构问题 - V2 版本使用更智能的修复策略"""

    def __init__(self, config: SkillConfig):
        super().__init__(config)
        self.min_cell_content = config.parameters.get("min_cell_content", 1)

    async def execute(self, middle_json: dict, issues: list = None, **kwargs) -> dict:
        """修复表格结构问题"""
        if not self.validate_input(middle_json):
            raise ValueError("Invalid middle_json input")

        if not issues:
            return middle_json

        table_issues = [
            i for i in issues
            if i.get("type") == "table_structure_invalid"
        ]

        for issue in table_issues:
            page_idx = issue.get("page", -1)
            block_idx = issue.get("block_index", -1)

            middle_json = self._fix_table_html(middle_json, page_idx, block_idx)

        return middle_json

    def validate_input(self, data: Any) -> bool:
        return isinstance(data, dict) and "pdf_info" in data

    def _fix_table_html(self, middle_json: dict, page_idx: int, block_idx: int) -> dict:
        """使用智能策略修复表格"""
        pages = middle_json.get("pdf_info", [])

        for page in pages:
            if page.get("page_idx") != page_idx:
                continue

            blocks = page.get("preproc_blocks", [])
            if block_idx >= len(blocks):
                return middle_json

            block = blocks[block_idx]
            if block.get("type") != "table":
                return middle_json

            html = block.get("html", "")

            # 分析表格结构
            row_count = html.count("<tr")
            cell_count = html.count("<td") + html.count("<th")

            # 如果行列数合理但结构分低，尝试修复
            if row_count > 0 and cell_count > 0:
                structure_score = min((row_count * cell_count) / 100, 1.0)

                if structure_score < 0.5:
                    # 结构太乱，简化为文本
                    block["html"] = self._table_to_text(html)
                    block["_repaired"] = True
                    block["_repair_method"] = "text_fallback"

            return middle_json

        return middle_json

    def _table_to_text(self, html: str) -> str:
        """将表格转换为文本"""
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'\s+', ' ', text)
        return f"[TABLE]\n{text.strip()}\n[/TABLE]"