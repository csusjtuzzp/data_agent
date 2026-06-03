# Copyright (c) Data Agent Team. All rights reserved.
"""ParseSkill for post-processing parsed documents."""

from typing import Any

from data_agent.skills.base_skill import BaseSkill, SkillConfig


class ParseSkill(BaseSkill):
    """Skill for post-processing parsed documents.

    This skill does NOT parse documents - it only does post-processing
    on already-parsed middle_json. Use DocumentParser sub-agent for actual parsing.
    """

    def __init__(self, config: SkillConfig):
        super().__init__(config)
        self.extract_images = config.parameters.get("extract_images", True)
        self.extract_tables = config.parameters.get("extract_tables", True)
        self.clean_empty_pages = config.parameters.get("clean_empty_pages", True)

    async def execute(self, data: Any, **kwargs) -> Any:
        """Apply parsing post-processing."""
        if not self.validate_input(data):
            raise ValueError("Invalid middle_json input")

        result = data.copy()

        if self.clean_empty_pages:
            result = self._remove_empty_pages(result)

        if self.extract_tables:
            result = self._extract_table_metadata(result)

        return result

    def validate_input(self, data: Any) -> bool:
        """Validate middle_json structure."""
        return isinstance(data, dict) and "pdf_info" in data

    def _remove_empty_pages(self, middle_json: dict) -> dict:
        """Remove pages with no content.

        A page is considered empty if it has neither preproc_blocks (PDF format)
        nor para_blocks (office format: docx/pptx/xlsx).
        """
        pdf_info = middle_json.get("pdf_info", [])
        cleaned = []
        for p in pdf_info:
            # Check preproc_blocks (PDF format) and para_blocks (office format)
            has_preproc = bool(p.get("preproc_blocks"))
            has_para = bool(p.get("para_blocks"))
            if has_preproc or has_para:
                cleaned.append(p)
        middle_json["pdf_info"] = cleaned
        return middle_json

    def _extract_table_metadata(self, middle_json: dict) -> dict:
        """Extract and mark table blocks.

        Tables can be in preproc_blocks (PDF format) or para_blocks (office format).
        """
        for page in middle_json.get("pdf_info", []):
            # Check preproc_blocks (PDF format)
            for block in page.get("preproc_blocks", []):
                if block.get("type") == "table":
                    block["_has_table"] = True
            # Check para_blocks (office format)
            for block in page.get("para_blocks", []):
                if block.get("type") == "table":
                    block["_has_table"] = True
        return middle_json