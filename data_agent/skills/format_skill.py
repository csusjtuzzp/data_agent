# Copyright (c) Data Agent Team. All rights reserved.
"""FormatSkill for formatting and transforming middle_json."""

from typing import Any

from data_agent.skills.base_skill import BaseSkill, SkillConfig


class FormatSkill(BaseSkill):
    """Skill for formatting and transforming middle_json."""

    TRANSFORMATIONS = {
        "flatten": "_flatten_structure",
        "nested": "_nest_structure",
        "extract_text": "_extract_text_only",
        "extract_images": "_extract_images_only",
    }

    def __init__(self, config: SkillConfig):
        super().__init__(config)
        self.default_transformation = config.parameters.get(
            "default", "flatten"
        )

    async def execute(
        self, data: Any, transformation: str = None, **kwargs
    ) -> Any:
        """Apply format transformation."""
        if not self.validate_input(data):
            raise ValueError("Invalid middle_json input")

        transform = transformation or self.default_transformation
        transform_func_name = self.TRANSFORMATIONS.get(
            transform, "_flatten_structure"
        )
        transform_func = getattr(self, transform_func_name)
        return transform_func(data)

    def validate_input(self, data: Any) -> bool:
        """Validate middle_json structure."""
        return isinstance(data, dict) and "pdf_info" in data

    def _flatten_structure(self, middle_json: dict) -> dict:
        """Flatten nested structure for easier processing."""
        return {
            "pdf_info": middle_json.get("pdf_info", []),
            "_backend": middle_json.get("_backend"),
            "_version": middle_json.get("_version_name"),
        }

    def _nest_structure(self, middle_json: dict) -> dict:
        """Create nested structure with pages as primary key."""
        return {
            "pages": {
                page.get("page_idx", i): page
                for i, page in enumerate(middle_json.get("pdf_info", []))
            },
            "_backend": middle_json.get("_backend"),
            "_version": middle_json.get("_version_name"),
        }

    def _extract_text_only(self, middle_json: dict) -> dict:
        """Extract text content only."""
        text_pages = []
        for page in middle_json.get("pdf_info", []):
            page_text = []
            for block in page.get("preproc_blocks", []):
                if block.get("type") == "text":
                    page_text.append(block.get("text", ""))
            text_pages.append("\n".join(page_text))
        return {
            "text": "\n\n".join(text_pages),
            "_backend": middle_json.get("_backend"),
        }

    def _extract_images_only(self, middle_json: dict) -> dict:
        """Extract images only."""
        images = []
        for page in middle_json.get("pdf_info", []):
            for block in page.get("preproc_blocks", []):
                if block.get("type") == "image":
                    images.append(
                        {
                            "page": page.get("page_idx"),
                            "path": block.get("path"),
                            "bbox": block.get("bbox"),
                        }
                    )
        return {"images": images, "_backend": middle_json.get("_backend")}
