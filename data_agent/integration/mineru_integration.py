"""
MinerU MiddleJson Integration Adapter.

Converts between MinerU's native middle_json structure
and the RuntimeState model.
"""

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from data_agent.state.runtime_state import RuntimeState, MiddleJson, PageInfo, Block


class MinerUMiddleJsonAdapter:
    """
    Adapter for MinerU middle_json format.

    Converts between MinerU's native middle_json structure
    and the RuntimeState model.
    """

    @staticmethod
    def to_runtime_state(task_id: str, middle_json: dict) -> "RuntimeState":
        """Convert MinerU middle_json to RuntimeState."""
        from data_agent.state.runtime_state import RuntimeState

        return RuntimeState(
            task_id=task_id,
            middle_json=MinerUMiddleJsonAdapter._dict_to_middle_json(middle_json),
        )

    @staticmethod
    def from_runtime_state(state: "RuntimeState") -> dict:
        """Convert RuntimeState back to MinerU middle_json format."""
        return state.middle_json.model_dump()

    @staticmethod
    def _dict_to_middle_json(data: dict) -> "MiddleJson":
        """Convert dict to MiddleJson model."""
        from data_agent.state.runtime_state import MiddleJson

        if isinstance(data, MiddleJson):
            return data

        pdf_info = data.get("pdf_info", [])
        normalized_pages = [
            MinerUMiddleJsonAdapter._normalize_page(p) for p in pdf_info
        ]

        return MiddleJson(
            pdf_info=normalized_pages,
            _backend=data.get("_backend", "unknown"),
            _version_name=data.get("_version_name", "1.0.0"),
            **{
                k: v for k, v in data.items()
                if k.startswith("_") and k not in ("_backend", "_version_name")
            },
        )

    @staticmethod
    def _normalize_page(page_dict: dict) -> "PageInfo":
        """Normalize a page dict to PageInfo model."""
        from data_agent.state.runtime_state import PageInfo, Block

        preproc_blocks = [
            Block(**block) if isinstance(block, dict) else block
            for block in page_dict.get("preproc_blocks", [])
        ]

        return PageInfo(
            preproc_blocks=preproc_blocks,
            para_blocks=page_dict.get("para_blocks", []),
            discarded_blocks=page_dict.get("discarded_blocks", []),
            page_size=tuple(page_dict.get("page_size", [0, 0])),
            page_idx=page_dict.get("page_idx", 0),
        )

    @staticmethod
    def merge_pages(pages: list[dict]) -> dict:
        """Merge multiple page dicts into a single middle_json."""
        return {
            "pdf_info": pages,
            "_backend": "merged",
            "_version_name": "1.0.0",
        }

    @staticmethod
    def split_by_page(middle_json: dict) -> list[dict]:
        """Split middle_json into individual page dicts."""
        pages = []
        for page in middle_json.get("pdf_info", []):
            pages.append({
                "pdf_info": [page],
                "_backend": middle_json.get("_backend", "unknown"),
                "_version_name": middle_json.get("_version_name", "1.0.0"),
            })
        return pages

    @staticmethod
    def extract_text(middle_json: dict) -> str:
        """Extract all text content from middle_json."""
        text_parts = []

        for page in middle_json.get("pdf_info", []):
            for block in page.get("preproc_blocks", []):
                block_type = block.get("type", "")
                if block_type in ("text", "title", "paragraph"):
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            if span.get("content"):
                                text_parts.append(span["content"])

        return "\n".join(text_parts)

    @staticmethod
    def get_stats(middle_json: dict) -> dict:
        """Get statistics about middle_json."""
        total_blocks = 0
        block_types = {}
        empty_pages = 0

        for page in middle_json.get("pdf_info", []):
            blocks = page.get("preproc_blocks", [])
            total_blocks += len(blocks)

            for block in blocks:
                block_type = block.get("type", "unknown")
                block_types[block_type] = block_types.get(block_type, 0) + 1

            if not blocks:
                empty_pages += 1

        return {
            "page_count": len(middle_json.get("pdf_info", [])),
            "total_blocks": total_blocks,
            "block_types": block_types,
            "empty_pages": empty_pages,
            "backend": middle_json.get("_backend", "unknown"),
        }