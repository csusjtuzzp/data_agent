# Copyright (c) Data Agent Team. All rights reserved.
"""Document Parser sub-agent using MinerU."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import pypdfium2.raw as pdfium_c
from loguru import logger

from data_agent.agent.base import AgentContext, AgentResponse, AgentStatus
from data_agent.agent.sub_agents.base_sub_agent import BaseSubAgent

if TYPE_CHECKING:
    from data_agent.integration.backend_selector import BackendSelector
    from data_agent.integration.mineru_client import MinerUClient
    from data_agent.skills.registry import SkillRegistry


@dataclass
class ParseResult:
    """Result of document parsing."""

    middle_json: dict
    backend: str
    doc_type: str
    metadata: dict


class DocumentParser(BaseSubAgent):
    """Sub-agent for document parsing using MinerU."""

    SUPPORTED_FORMATS = {
        ".pdf": "pdf",
        ".docx": "docx",
        ".pptx": "pptx",
        ".xlsx": "xlsx",
        ".html": "html",
        ".htm": "html",
        ".png": "image",
        ".jpg": "image",
        ".jpeg": "image",
    }

    def __init__(
        self,
        skill_registry: Optional["SkillRegistry"] = None,
        mineru_client: Optional["MinerUClient"] = None,
        backend_selector: Optional["BackendSelector"] = None,
        miner_parse_output_dir: Optional[str] = None,
    ):
        super().__init__("DocumentParser", skill_registry)
        self.mineru_client = mineru_client
        self.backend_selector = backend_selector
        self.parse_skill = None
        self._dedup_skill = None
        self._cache_dir = "/tmp/mineru_parse_cache"
        self._miner_parse_output_dir = miner_parse_output_dir  # Local output directory for mineru parse results

    def _timeline_log(self, context: AgentContext, level: str, action: str, status=None, extra: dict = None):
        """Log to timeline_logger if available."""
        tl = context.timeline_logger if context else None
        if not tl:
            return

        # Build extended message with extra info
        msg = action
        if extra:
            extra_str = ", ".join(f"{k}={v}" for k, v in extra.items() if v is not None)
            if extra_str:
                msg = f"{action} | {extra_str}"

        if status == "success":
            tl.success("DocumentParser", msg, sub_agent="DocumentParser")
        elif status == "error":
            tl.error("DocumentParser", msg, sub_agent="DocumentParser")
        elif status == "running":
            tl.running("DocumentParser", msg, sub_agent="DocumentParser")
        else:
            tl.running("DocumentParser", msg, sub_agent="DocumentParser")

    async def execute(self, context: AgentContext) -> AgentResponse:
        await self.pre_execute(context)

        input_data = context.original_input

        # Timeline: Start parsing with request info
        file_path = input_data.get("path") or input_data.get("url") or "unknown"
        file_name = Path(file_path).name if file_path != "unknown" else "unknown"
        self._timeline_log(
            context, "info",
            f"Starting document parsing",
            extra={"file": file_name, "path": file_path, "input_keys": list(input_data.keys())}
        )

        # input_data 标准化为 {"path": "...", "filename": "..."} 或 {"path": "..."} 或 {"data": {"path": ..., "filename": ...}}
        # 处理 LLM planner 输出的嵌套结构
        if "data" in input_data and isinstance(input_data["data"], dict):
            input_data = input_data["data"]

        file_path = input_data.get("path")

        if not file_path:
            file_path = input_data.get("url")

        if not file_path:
            logger.warning(f"[DocumentParser] No file path found, input_data keys: {list(input_data.keys())}")
            self._timeline_log(context, "error", f"No file path provided", status="error")
            return AgentResponse(
                success=False,
                status=AgentStatus.FAILED,
                error=f"No file path provided",
                context=context,
            )

        try:
            file_ext = Path(file_path).suffix.lower()
            doc_type = self.SUPPORTED_FORMATS.get(file_ext, "unknown")

            if doc_type == "unknown":
                self._timeline_log(context, "error", f"Unsupported file type: {file_ext}", status="error")
                return AgentResponse(
                    success=False,
                    status=AgentStatus.FAILED,
                    error=f"Unsupported file type: {file_ext}",
                    context=context,
                )

            backend = await self._select_backend(
                file_path=file_path,
                doc_type=doc_type,
                config=context.metadata,
                context=context,
            )

            # Timeline: Backend selected
            self._timeline_log(context, "info", f"Selected backend: {backend} for {doc_type}")
            logger.info(f"[DocumentParser] Selected backend: {backend}")

            # Check cache before parsing
            cached_result = await self._check_cache(file_path)
            if cached_result:
                # Get cache path for logging (use full hash for accuracy)
                file_hash = self._compute_file_hash(file_path)
                cache_path = f"{self._cache_dir}/{file_hash}/middle.json" if file_hash else "unknown"

                # Timeline: Cache hit with details
                cached_page_count = len(cached_result["middle_json"].get("pdf_info", []))
                cached_backend = cached_result.get("backend", "unknown")
                self._timeline_log(
                    context, "info",
                    f"Cache hit, returning cached result",
                    status="success",
                    extra={
                        "cache_path": cache_path,
                        "backend": cached_backend,
                        "page_count": cached_page_count,
                        "doc_type": doc_type,
                    }
                )
                logger.info(f"[DocumentParser] Cache hit, returning cached middle_json")

                output = {
                    "middle_json": cached_result["middle_json"],
                    "backend": cached_result.get("backend", backend),
                    "doc_type": doc_type,
                    "metadata": {
                        "file_path": str(file_path),
                        "page_count": cached_page_count,
                        "cache_hit": True,
                        "backend": cached_result.get("backend", backend),
                        "cache_path": cache_path,
                    },
                }
                return AgentResponse(
                    success=True,
                    status=AgentStatus.COMPLETED,
                    output=output,
                    context=context,
                )

            # Timeline: Cache miss, starting parse
            self._timeline_log(context, "info", f"Cache miss, starting real parsing", extra={"backend": backend, "doc_type": doc_type})

            # Per-page backend selection for PDFs
            if doc_type == "pdf":
                middle_json = await self._parse_with_per_page_backends(
                    file_path=file_path,
                    context=context,
                    options=input_data.get("options", {}),
                )
            else:
                middle_json, _ = await self.mineru_client.parse(
                    file_path=file_path,
                    backend=backend,
                    return_middle_json=True,
                    return_model_output=True,
                    **(input_data.get("options", {})),
                )

            if self.parse_skill:
                middle_json = await self.parse_skill.execute(middle_json)

            page_count = len(middle_json.get("pdf_info", []))
            output = {
                "middle_json": middle_json,
                "backend": backend,
                "doc_type": doc_type,
                "metadata": {
                    "file_path": str(file_path),
                    "page_count": page_count,
                    "backend": backend,
                },
            }

            # Store result in cache for future deduplication
            await self._store_cache(file_path, middle_json, backend)

            # Timeline: Parse completed
            self._timeline_log(context, "info", f"Parsing completed: {page_count} pages with {backend}", status="success")

            return AgentResponse(
                success=True,
                status=AgentStatus.COMPLETED,
                output=output,
                context=context,
            )

        except Exception as e:
            import traceback
            logger.error(f"Document parsing failed: {traceback.format_exc()}")
            self._timeline_log(context, "error", f"Parse failed: {str(e)}", status="error")
            return AgentResponse(
                success=False,
                status=AgentStatus.FAILED,
                error=str(e),
                context=context,
            )

    async def _select_backend(
        self, file_path: str, doc_type: str, config: dict, context: AgentContext = None
    ) -> str:
        """Select appropriate backend for the document."""
        if doc_type == "html":
            return "html"

        if self.backend_selector:
            tl = context.timeline_logger if context else None
            result = await self.backend_selector.select_backend(
                file_path=file_path,
                doc_type=doc_type,
                config=config,
                timeline_logger=tl,
            )
            return result.backend

        return "hybrid-auto-engine"

    def _compute_file_hash(self, file_path: str) -> Optional[str]:
        """Compute SHA256 hash of file content"""
        try:
            hasher = hashlib.sha256()
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except Exception as e:
            import traceback
            logger.error(f"[DocumentParser] Failed to compute hash for {file_path}: {traceback.format_exc()}")
            return None

    async def _check_cache(self, file_path: str) -> Optional[dict]:
        """Check if file has cached parse result"""
        try:
            file_hash = self._compute_file_hash(file_path)
            if not file_hash:
                return None

            import json
            import os

            # First check system cache /tmp/mineru_parse_cache
            cache_subdir = os.path.join(self._cache_dir, file_hash)
            middle_json_path = os.path.join(cache_subdir, "middle.json")
            meta_json_path = os.path.join(cache_subdir, "meta.json")

            if os.path.exists(middle_json_path):
                with open(middle_json_path, "r", encoding="utf-8") as f:
                    middle_json = json.load(f)

                metadata = None
                if os.path.exists(meta_json_path):
                    with open(meta_json_path, "r", encoding="utf-8") as f:
                        metadata = json.load(f)

                logger.info(f"[DocumentParser] Cache hit for {file_hash[:16]}... (system cache)")
                return {
                    "middle_json": middle_json,
                    "backend": metadata.get("backend") if metadata else None,
                }

            # If not found in system cache, check local mineru_parse output directory
            if self._miner_parse_output_dir:
                local_middle_json_path = os.path.join(self._miner_parse_output_dir, "middle.json")
                if os.path.exists(local_middle_json_path):
                    with open(local_middle_json_path, "r", encoding="utf-8") as f:
                        middle_json = json.load(f)

                    logger.info(f"[DocumentParser] Cache hit for {file_hash[:16]}... (local mineru_parse)")
                    return {
                        "middle_json": middle_json,
                        "backend": middle_json.get("backend", None),
                    }

            logger.info(f"[DocumentParser] Cache miss for {file_hash[:16]}...")
            return None

        except Exception as e:
            import traceback
            logger.error(f"[DocumentParser] Cache check failed: {traceback.format_exc()}")
            return None

    async def _store_cache(self, file_path: str, middle_json: dict, backend: str) -> None:
        """Store parse result in cache"""
        try:
            import os
            from pathlib import Path

            def json_serializer(obj):
                """Custom JSON serializer for non-serializable objects."""
                if hasattr(obj, 'value'):  # Enum with .value
                    return obj.value
                if hasattr(obj, '__dict__'):
                    return obj.__dict__
                raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

            file_hash = self._compute_file_hash(file_path)
            if not file_hash:
                return

            cache_subdir = os.path.join(self._cache_dir, file_hash)
            Path(cache_subdir).mkdir(parents=True, exist_ok=True)

            middle_json_path = os.path.join(cache_subdir, "middle.json")
            meta_json_path = os.path.join(cache_subdir, "meta.json")

            with open(middle_json_path, "w", encoding="utf-8") as f:
                json.dump(middle_json, f, ensure_ascii=False, default=json_serializer)

            metadata = {
                "file_hash": file_hash,
                "file_path": str(file_path),
                "backend": backend,
            }
            with open(meta_json_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False)

            logger.info(f"[DocumentParser] Stored parse result in cache for {file_hash[:16]}...")

            # Also save to local mineru_parse output directory if set
            if self._miner_parse_output_dir:
                local_middle_json_path = os.path.join(self._miner_parse_output_dir, "middle.json")
                try:
                    with open(local_middle_json_path, "w", encoding="utf-8") as f:
                        json.dump(middle_json, f, ensure_ascii=False, default=json_serializer)
                    logger.info(f"[DocumentParser] Also saved middle.json to local mineru_parse: {local_middle_json_path}")
                except Exception as e:
                    logger.warning(f"[DocumentParser] Failed to save to local mineru_parse: {e}")

        except Exception as e:
            import traceback
            logger.error(f"[DocumentParser] Failed to store cache: {traceback.format_exc()}")

    async def _analyze_page_complexity(self, file_path: str, doc_type: str) -> list[tuple[int, float]]:
        """Analyze each page's complexity using direct PDF inspection.

        Returns:
            List of (page_idx, complexity_score) tuples.
            Complexity score: 0.0 (simplest) to 1.0 (most complex).
        """
        import pypdfium2 as pdfium

        logger.info(f"[DocumentParser] Analyzing page complexity for {file_path}")

        try:
            pdf = pdfium.PdfDocument(file_path)
            page_count = len(pdf)
            complexity_scores = []

            for page_idx in range(page_count):
                page = pdf[page_idx]
                textpage = page.get_textpage()
                text_content = textpage.get_text_bounded()

                # Factor 1: Text length (low text = high complexity, likely scanned)
                text_len = len(text_content.strip())
                text_score = 0.0
                if text_len < 100:
                    text_score = 0.4  # Very low text → likely scanned
                elif text_len < 300:
                    text_score = 0.2  # Low text → possibly scanned

                # Factor 2: Page size (very large pages may be complex)
                try:
                    page_size = page.get_size()
                    page_area = page_size[0] * page_size[1] if page_size else 1
                    # A4 at 72dpi ≈ 595 x 842 = 500900, normalize
                    size_score = min((page_area / 500000) * 0.1, 0.1)
                except Exception:
                    size_score = 0.0

                # Factor 3: Image content (check for image XObjects)
                image_score = 0.0
                try:
                    page_objects = page.get_objects()
                    for obj in page_objects:
                        if obj.type == pdfium_c.FPDF_PAGEOBJ_IMAGE:
                            image_score = 0.3
                            break
                except Exception:
                    pass

                # Combine scores
                complexity = min(text_score + size_score + image_score, 1.0)
                complexity_scores.append((page_idx, complexity))

                logger.debug(f"[DocumentParser] Page {page_idx}: text_len={text_len}, complexity={complexity:.2f}")

            pdf.close()
            return complexity_scores

        except Exception as e:
            import traceback
            logger.warning(f"[DocumentParser] Complexity analysis failed: {traceback.format_exc()}")
            # Return default complexity (0.5) for all pages on error
            return [(i, 0.5) for i in range(1)]

    def _assign_page_backends(self, page_complexity: list[tuple[int, float]]) -> list[tuple[int, str]]:
        """Assign backends to pages based on complexity scores.

        Args:
            page_complexity: List of (page_idx, complexity_score) tuples

        Returns:
            List of (page_idx, backend) tuples
        """
        backend_map = {
            (0.0, 0.3): "pipeline",        # Simple: text-heavy, no images
            (0.3, 0.7): "hybrid-auto-engine",  # Medium: mixed content
            (0.7, 1.0): "vlm-auto-engine",    # Complex: scanned, images
        }

        assignments = []
        for page_idx, complexity in page_complexity:
            for (low, high), backend in backend_map.items():
                if low <= complexity < high:
                    assignments.append((page_idx, backend))
                    break
            else:
                # Default to hybrid for edge cases
                assignments.append((page_idx, "hybrid-auto-engine"))

        logger.info(f"[DocumentParser] Backend assignments: {assignments}")
        return assignments

    def _group_pages_by_backend(self, page_backend: list[tuple[int, str]]) -> list[tuple[int, int, str]]:
        """Group consecutive pages with same backend into ranges.

        Args:
            page_backend: List of (page_idx, backend) tuples

        Returns:
            List of (start_page, end_page, backend) tuples for API calls
        """
        if not page_backend:
            return []

        # Sort by page index
        sorted_pages = sorted(page_backend, key=lambda x: x[0])

        ranges = []
        current_start = sorted_pages[0][0]
        current_end = sorted_pages[0][0]
        current_backend = sorted_pages[0][1]

        for page_idx, backend in sorted_pages[1:]:
            if backend == current_backend and page_idx == current_end + 1:
                # Consecutive page with same backend
                current_end = page_idx
            else:
                # End current range, start new one
                ranges.append((current_start, current_end, current_backend))
                current_start = page_idx
                current_end = page_idx
                current_backend = backend

        # Don't forget the last range
        ranges.append((current_start, current_end, current_backend))

        logger.info(f"[DocumentParser] Grouped page ranges: {ranges}")
        return ranges

    async def _parse_with_per_page_backends(
        self,
        file_path: str,
        context: AgentContext,
        options: dict,
    ) -> dict:
        """Parse PDF with different backends per page based on complexity analysis.

        Phase 1: Analyze page complexity using direct PDF inspection
        Phase 2: Assign backends based on complexity
        Phase 3: Parse pages grouped by backend and merge results
        """
        # Phase 1: Analyze complexity
        self._timeline_log(context, "info", "Analyzing page complexity...")
        page_complexity = await self._analyze_page_complexity(file_path, "pdf")

        # Phase 2: Assign backends per page
        page_backends = self._assign_page_backends(page_complexity)
        self._timeline_log(context, "info", f"Assigned backends to {len(page_backends)} pages")

        # Phase 3: Group consecutive pages with same backend
        page_ranges = self._group_pages_by_backend(page_backends)
        self._timeline_log(context, "info", f"Grouped into {len(page_ranges)} page ranges for parsing")

        logger.info(f"[DocumentParser] Parsing {len(page_ranges)} page ranges with different backends")

        # Timeline: Start parsing with per-page backends
        self._timeline_log(context, "info", f"Starting MinerU parse with per-page backends")

        # Parse using MinerUClient.parse_pages
        middle_json, _ = await self.mineru_client.parse_pages(
            file_path=file_path,
            page_backend_ranges=page_ranges,
            return_middle_json=True,
            return_model_output=False,
            **options,
        )

        # Add metadata about per-page backend selection
        backend_per_page = {page_idx: backend for page_idx, backend in page_backends}
        middle_json["_backend_per_page"] = backend_per_page
        middle_json["_per_page_backend_selection"] = True

        return middle_json
