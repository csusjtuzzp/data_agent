# Copyright (c) Data Agent Team. All rights reserved.
"""Backend selector for auto-selecting optimal MinerU backend."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger

# Optional dependency for PDF analysis
try:
    import pypdfium2 as pdfium
    PDFIUM_AVAILABLE = True
except ImportError:
    PDFIUM_AVAILABLE = False
    pdfium = None


@dataclass
class BackendSelectionResult:
    """Result of backend selection."""

    backend: str
    confidence: float
    reasoning: str


class BackendSelector:
    """Auto-selects optimal MinerU backend based on document characteristics."""

    BACKEND_PRIORITY = {
        "pdf": ["hybrid-auto-engine", "vlm-auto-engine", "pipeline"],
        "docx": ["office"],
        "pptx": ["office"],
        "xlsx": ["office"],
        "image": ["vlm-auto-engine", "hybrid-auto-engine"],
    }

    def __init__(self):
        self._fallback_map = {
            "hybrid-auto-engine": "vlm-auto-engine",
            "vlm-auto-engine": "pipeline",
            "pipeline": "vlm-auto-engine",
            "vlm-http-client": "hybrid-http-client",
            "hybrid-http-client": "vlm-http-client",
        }

    async def select_backend(
        self,
        file_path: str,
        doc_type: str,
        config: Optional[dict] = None,
        timeline_logger=None,
    ) -> BackendSelectionResult:
        """Select best backend for document."""
        config = config or {}

        preferred = config.get("preferred_backend")
        if preferred:
            return BackendSelectionResult(
                backend=preferred,
                confidence=1.0,
                reasoning=f"User specified: {preferred}",
            )

        if doc_type == "pdf":
            return await self._select_pdf_backend(file_path, config)
        elif doc_type == "image":
            return BackendSelectionResult(
                backend="vlm-auto-engine",
                confidence=0.9,
                reasoning="Image content - VLM recommended",
            )
        else:
            # docx, pptx, xlsx all use hybrid-auto-engine
            return BackendSelectionResult(
                backend="hybrid-auto-engine",
                confidence=1.0,
                reasoning="Using hybrid-auto-engine for office formats",
            )

    async def _select_pdf_backend(
        self, file_path: str, config: dict
    ) -> BackendSelectionResult:
        """Select backend for PDF based on characteristics."""
        try:
            page_count = self._get_page_count(file_path)
            has_images = self._has_image_content(file_path)

            if page_count > 50:
                return BackendSelectionResult(
                    backend="pipeline",
                    confidence=0.8,
                    reasoning=f"Large PDF ({page_count} pages) - pipeline more stable",
                )
            elif has_images:
                return BackendSelectionResult(
                    backend="hybrid-auto-engine",
                    confidence=0.9,
                    reasoning="Image content detected - hybrid best quality",
                )
            else:
                return BackendSelectionResult(
                    backend="hybrid-auto-engine",
                    confidence=0.85,
                    reasoning="Default selection for balanced quality/speed",
                )
        except Exception as e:
            return BackendSelectionResult(
                backend="pipeline",
                confidence=0.5,
                reasoning=f"Fallback due to analysis error: {e}",
            )

    def _get_page_count(self, file_path: str) -> int:
        """Get PDF page count."""
        if not PDFIUM_AVAILABLE:
            return 1

        try:
            pdf_doc = pdfium.PdfDocument(file_path)
            return len(pdf_doc)
        except Exception as e:
            import traceback
            logger.warning(f"Failed to get page count: {traceback.format_exc()}")
            return 1

    def _has_image_content(self, file_path: str) -> bool:
        """Check if PDF contains significant image content."""
        if not PDFIUM_AVAILABLE:
            return True  # Conservative default

        try:
            pdf_doc = pdfium.PdfDocument(file_path)
            for page in pdf_doc:
                page.GetObjects()
            return True
        except Exception:
            return True

    def get_fallback_backend(self, current: str) -> str:
        """Get fallback backend for a given backend."""
        return self._fallback_map.get(current, "pipeline")
