# Copyright (c) Data Agent Team. All rights reserved.
"""FilterSkill for filtering and cleaning middle_json data."""

from typing import Any

from data_agent.skills.base_skill import BaseSkill, SkillConfig


class FilterSkill(BaseSkill):
    """Skill for filtering and cleaning middle_json data."""

    def __init__(self, config: SkillConfig):
        super().__init__(config)
        self.min_confidence = config.parameters.get("min_confidence", 0.5)
        self.allowed_types = config.parameters.get("allowed_types", None)
        self.remove_duplicates = config.parameters.get("remove_duplicates", True)

    async def execute(self, data: Any, **kwargs) -> Any:
        """Apply filtering to middle_json."""
        if not self.validate_input(data):
            raise ValueError("Invalid middle_json input")

        result = data.copy()

        if self.remove_duplicates:
            result = self._deduplicate(result)

        if self.allowed_types:
            result = self._filter_by_type(result)

        result = self._filter_by_confidence(result)

        return result

    def validate_input(self, data: Any) -> bool:
        """Validate middle_json structure."""
        return isinstance(data, dict) and "pdf_info" in data

    def _deduplicate(self, middle_json: dict) -> dict:
        """Remove duplicate blocks."""
        seen = set()
        deduped_pages = []

        for page in middle_json.get("pdf_info", []):
            deduped_blocks = []
            for block in page.get("preproc_blocks", []):
                block_key = self._get_block_key(block)
                if block_key not in seen:
                    seen.add(block_key)
                    deduped_blocks.append(block)
            page["preproc_blocks"] = deduped_blocks
            deduped_pages.append(page)

        middle_json["pdf_info"] = deduped_pages
        return middle_json

    def _get_block_key(self, block: dict) -> str:
        """Generate a unique key for a block."""
        block_type = block.get("type", "")
        text = block.get("text", "")[:100]
        return f"{block_type}:{text}"

    def _filter_by_type(self, middle_json: dict) -> dict:
        """Filter blocks by allowed types."""
        if not self.allowed_types:
            return middle_json

        filtered_pages = []
        for page in middle_json.get("pdf_info", []):
            filtered_blocks = [
                b
                for b in page.get("preproc_blocks", [])
                if b.get("type") in self.allowed_types
            ]
            page["preproc_blocks"] = filtered_blocks
            filtered_pages.append(page)

        middle_json["pdf_info"] = filtered_pages
        return middle_json

    def _filter_by_confidence(self, middle_json: dict) -> dict:
        """Filter blocks by confidence score."""
        filtered_pages = []
        for page in middle_json.get("pdf_info", []):
            filtered_blocks = [
                b
                for b in page.get("preproc_blocks", [])
                if b.get("confidence", 1.0) >= self.min_confidence
            ]
            page["preproc_blocks"] = filtered_blocks
            filtered_pages.append(page)

        middle_json["pdf_info"] = filtered_pages
        return middle_json
