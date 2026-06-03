# Copyright (c) Data Agent Team. All rights reserved.
"""ReparseBadPagesSkill - Re-parse only the problematic pages and merge results."""

from typing import Any, Optional

from loguru import logger

from data_agent.skills.base_skill import BaseSkill, SkillConfig


class ReparseBadPagesSkill(BaseSkill):
    """Re-parse only the bad quality pages and merge with good pages.

    This skill:
    1. Identifies bad pages from middle_json quality markers
    2. Re-parses only those pages using MinerU API's start_page_id/end_page_id
    3. Merges re-parsed good pages with original good pages
    4. Updates middle_json with the merged result
    """

    def __init__(self, config: SkillConfig):
        super().__init__(config)
        self.min_text_length = config.parameters.get("min_text_length", 50)
        self.min_block_count = config.parameters.get("min_block_count", 1)
        self.chunk_size = config.parameters.get("chunk_size", 5)  # max pages per reparse call
        self.api_url = config.parameters.get("api_url", "")

    async def execute(
        self,
        middle_json: dict,
        file_path: str = None,
        backend: str = "hybrid-auto-engine",
        mineru_client: Any = None,
        **kwargs
    ) -> dict:
        """Execute reparse on bad pages.

        Args:
            middle_json: The current middle_json with quality markers
            file_path: Path to original file (needed if not in middle_json)
            backend: MinerU backend to use for re-parsing
            mineru_client: MinerUClient instance for API calls (optional, will create if not provided)
        """
        if not self.validate_input(middle_json):
            raise ValueError("Invalid middle_json input")

        # Get file_path from middle_json metadata or kwargs
        file_path = file_path or middle_json.get("_file_path")
        if not file_path:
            logger.error("[ReparseBadPages] No file_path available")
            return middle_json

        # Identify bad pages
        bad_pages = self._identify_bad_pages(middle_json)
        if not bad_pages:
            logger.info("[ReparseBadPages] No bad pages to re-parse")
            return middle_json

        logger.info(f"[ReparseBadPages] Found {len(bad_pages)} bad pages: {bad_pages}")

        # Get good pages (pages that don't need reparse)
        good_pages = self._get_good_pages(middle_json)
        logger.info(f"[ReparseBadPages] Preserving {len(good_pages)} good pages")

        # Get or create mineru_client
        if not mineru_client:
            from data_agent.integration.mineru_client import MinerUClient
            mineru_client = MinerUClient(api_url=self.api_url)

        # Re-parse bad pages in chunks to avoid API limits
        for chunk in self._chunk_pages(bad_pages):
            try:
                logger.info(f"[ReparseBadPages] Reparsing pages {chunk[0]}-{chunk[-1]}")
                middle_json, _ = await mineru_client.parse(
                    file_path=file_path,
                    backend=backend,
                    start_page_id=chunk[0],
                    end_page_id=chunk[-1] + 1,  # end is exclusive in API
                    return_middle_json=True,
                    return_model_output=False,
                )

                # Merge re-parsed pages into good pages
                reparsed_pages = middle_json.get("pdf_info", []) if middle_json else []
                good_pages = self._merge_pages(good_pages, reparsed_pages, chunk[0])

            except Exception as e:
                import traceback
                logger.error(f"[ReparseBadPages] Reparse failed for chunk {chunk}: {traceback.format_exc()}")
                continue

        # Update middle_json with merged result
        middle_json["pdf_info"] = good_pages
        middle_json["_reparsed_pages"] = list(bad_pages)
        middle_json["_original_page_count"] = len(middle_json.get("pdf_info", [])) + len(bad_pages)

        logger.info(f"[ReparseBadPages] Merged result has {len(good_pages)} pages")
        return middle_json

    def _identify_bad_pages(self, middle_json: dict) -> list[int]:
        """Identify pages that need re-parsing.

        Checks multiple quality indicators:
        - _has_critical_issues marker
        - _has_issues marker
        - Empty pages (no preproc_blocks)
        - Low text content pages
        """
        bad_pages = []

        for page in middle_json.get("pdf_info", []):
            page_idx = page.get("page_idx", -1)
            if page_idx < 0:
                continue

            # Check quality markers from LowerScoreSkill
            if page.get("_has_critical_issues"):
                if page_idx not in bad_pages:
                    bad_pages.append(page_idx)
                continue

            # Check for empty blocks
            blocks = page.get("preproc_blocks", [])
            if not blocks or len(blocks) == 0:
                if page_idx not in bad_pages:
                    bad_pages.append(page_idx)
                continue

            # Check text content length
            text_len = sum(len(b.get("text", "")) for b in blocks)
            if text_len < self.min_text_length:
                if page_idx not in bad_pages:
                    bad_pages.append(page_idx)
                continue

            # Check block count
            content_blocks = [b for b in blocks if not self._is_layout_block(b)]
            if len(content_blocks) < self.min_block_count:
                if page_idx not in bad_pages:
                    bad_pages.append(page_idx)

        return bad_pages

    def _is_layout_block(self, block: dict) -> bool:
        """Check if block is a layout element (not content)."""
        layout_types = {
            "page_number", "header", "page_footnote", "page_footer",
            "separator", "aside_text", "margin"
        }
        return block.get("type", "") in layout_types

    def _get_good_pages(self, middle_json: dict) -> list[dict]:
        """Get pages that don't need re-parsing."""
        good_pages = []
        bad_pages_set = set(self._identify_bad_pages(middle_json))

        for page in middle_json.get("pdf_info", []):
            page_idx = page.get("page_idx", -1)
            if page_idx >= 0 and page_idx not in bad_pages_set:
                good_pages.append(page.copy() if isinstance(page, dict) else page)

        return good_pages

    def _chunk_pages(self, pages: list[int]) -> list[list[int]]:
        """Split pages into chunks for batch re-parsing."""
        chunks = []
        for i in range(0, len(pages), self.chunk_size):
            chunks.append(pages[i:i + self.chunk_size])
        return chunks

    def _merge_pages(
        self,
        good_pages: list[dict],
        reparsed_pages: list[dict],
        reparse_start_idx: int
    ) -> list[dict]:
        """Merge re-parsed pages with good pages.

        Args:
            good_pages: Original good pages to preserve
            reparsed_pages: Newly parsed pages from MinerU
            reparse_start_idx: Starting page index of the reparse range

        Returns:
            Merged page list
        """
        if not reparsed_pages:
            return good_pages

        # Build index to page mapping for good pages
        page_map = {p.get("page_idx", i): p for i, p in enumerate(good_pages)}

        # Replace/add reparsed pages
        for reparsed_page in reparsed_pages:
            page_idx = reparsed_page.get("page_idx", -1)
            if page_idx >= 0:
                page_map[page_idx] = reparsed_page

        # Sort by page_idx and return
        result = [page_map[idx] for idx in sorted(page_map.keys())]
        return result

    def validate_input(self, data: Any) -> bool:
        """Validate middle_json structure."""
        return isinstance(data, dict) and "pdf_info" in data