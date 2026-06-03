# Copyright (c) Data Agent Team. All rights reserved.
"""Middle JSON utilities."""

from typing import Any


class MiddleJsonHandler:
    """Handler for middle_json operations."""

    @staticmethod
    def normalize(middle_json: dict) -> dict:
        """Normalize middle_json to standard format."""
        if not isinstance(middle_json, dict):
            return {}

        normalized = {
            "pdf_info": middle_json.get("pdf_info", []),
            "_backend": middle_json.get("_backend", "unknown"),
            "_version_name": middle_json.get("_version_name", "1.0.0"),
        }
        return normalized

    @staticmethod
    def merge(outputs: list[dict]) -> dict:
        """Merge multiple middle_json outputs."""
        if not outputs:
            return {}

        merged = {
            "pdf_info": [],
            "_backend": "multi",
            "_version_name": "1.0.0",
        }

        for mj in outputs:
            if "pdf_info" in mj:
                merged["pdf_info"].extend(mj["pdf_info"])

        return merged

    @staticmethod
    def split(middle_json: dict, page_groups: int = 1) -> list[dict]:
        """Split middle_json into multiple parts."""
        if not middle_json:
            return []

        pages = middle_json.get("pdf_info", [])
        if page_groups <= 1:
            return [middle_json]

        split_size = max(1, len(pages) // page_groups)
        splits = []

        for i in range(0, len(pages), split_size):
            split_mj = middle_json.copy()
            split_mj["pdf_info"] = pages[i : i + split_size]
            splits.append(split_mj)

        return splits

    @staticmethod
    def get_text_content(middle_json: dict) -> str:
        """Extract all text content from middle_json."""
        text_parts = []

        for page in middle_json.get("pdf_info", []):
            page_text = []
            for block in page.get("preproc_blocks", []):
                if block.get("type") in ("text", "title"):
                    text = block.get("text", "")
                    if text:
                        page_text.append(text)
            text_parts.append("\n".join(page_text))

        return "\n\n".join(text_parts)

    @staticmethod
    def get_stats(middle_json: dict) -> dict:
        """Get statistics about the middle_json."""
        pdf_info = middle_json.get("pdf_info", [])
        total_blocks = sum(
            len(p.get("preproc_blocks", [])) for p in pdf_info
        )

        block_types = {}
        for page in pdf_info:
            for block in page.get("preproc_blocks", []):
                block_type = block.get("type", "unknown")
                block_types[block_type] = block_types.get(block_type, 0) + 1

        return {
            "page_count": len(pdf_info),
            "total_blocks": total_blocks,
            "block_types": block_types,
            "backend": middle_json.get("_backend"),
            "version": middle_json.get("_version_name"),
        }
