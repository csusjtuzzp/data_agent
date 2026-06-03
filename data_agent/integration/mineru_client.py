# Copyright (c) Data Agent Team. All rights reserved.
"""MinerU client wrapper using MinerU API for document parsing operations."""

import json as json_module
import mimetypes
import os
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from data_agent.utils.exceptions import ParsingError


@dataclass
class MinerUParseResult:
    """Result of MinerU parsing."""

    middle_json: dict
    model_output: Any
    backend: str
    processing_time: float


class MinerUClient:
    """Client wrapper for MinerU API operations."""

    def __init__(
        self,
        api_url: str = "",
        timeout: float = 3600.0,
    ):
        self.api_url = api_url.rstrip("/") if api_url else ""
        self.timeout = timeout

    async def parse(
        self,
        file_path: str,
        backend: str = "hybrid-auto-engine",
        return_middle_json: bool = True,
        return_model_output: bool = False,
        start_page_id: int = 0,
        end_page_id: int = 99999,
        **kwargs,
    ) -> tuple[dict, Any]:
        """Parse document using MinerU API with ZIP response format.

        Args:
            file_path: Path to the file to parse
            backend: Backend to use for parsing
            return_middle_json: Whether to return middle_json
            return_model_output: Whether to return model output
            start_page_id: Start page index (0-based)
            end_page_id: End page index (inclusive)
            **kwargs: Additional options
        """
        import httpx

        start_time = time.time()

        try:
            logger.info(f"[MinerU] Starting parse for {file_path} with backend={backend}")

            form_data = self._build_form_data(
                backend, return_middle_json, return_model_output,
                start_page_id=start_page_id, end_page_id=end_page_id, **kwargs
            )
            task_url = f"{self.api_url}/file_parse"
            logger.info(f"[MinerU] Submitting to {task_url}")

            with open(file_path, "rb") as f:
                file_bytes = f.read()

            mime_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
            filename = Path(file_path).name

            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=600, write=300, pool=30)) as client:
                response = await client.post(
                    task_url,
                    data=form_data,
                    files=[("files", (filename, file_bytes, mime_type))],
                )

            logger.info(f"[MinerU] Response status: {response.status_code}")

            # response_format_zip=true 时，响应体直接是 ZIP 文件
            content_type = response.headers.get("content-type", "")
            result = {}  # 用于返回给调用者

            if "zip" in content_type or "octet-stream" in content_type:
                # 响应体直接是 ZIP
                zip_fd, zip_path = tempfile.mkstemp(suffix=".zip", prefix="mineru_result_")
                os.close(zip_fd)

                with open(zip_path, "wb") as f:
                    f.write(response.content)

                logger.info(f"[MinerU] Downloaded ZIP directly to: {zip_path}")
            else:
                # JSON 响应，查找 result_url
                result = response.json()
                logger.info(f"[MinerU] Result keys: {list(result.keys())}")

                result_url = result.get("result_url")
                if not result_url:
                    raise ParsingError(f"No result_url in response: {result}")

                logger.info(f"[MinerU] Downloading result ZIP from: {result_url}")

                zip_fd, zip_path = tempfile.mkstemp(suffix=".zip", prefix="mineru_result_")
                os.close(zip_fd)

                async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=600, write=300, pool=30)) as client:
                    async with client.stream("GET", result_url) as resp:
                        if resp.status_code != 200:
                            raise ParsingError(f"Failed to download result: {resp.status_code}")
                        with open(zip_path, "wb") as f:
                            async for chunk in resp.aiter_bytes():
                                f.write(chunk)

                logger.info(f"[MinerU] Downloaded ZIP to: {zip_path}")

            # 解压并读取 middle_json
            extract_dir = tempfile.mkdtemp(prefix="mineru_extract_")
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)

            logger.info(f"[MinerU] Extracted to: {extract_dir}")

            # 查找 middle_json 文件
            # ZIP 结构: extract_dir/{filename}/{backend}/{filename}_middle.json
            middle_json = {}
            middle_json_path = None

            for root, dirs, files in os.walk(extract_dir):
                for fname in files:
                    # 匹配 *_middle.json 文件
                    if fname.endswith("_middle.json"):
                        middle_json_path = os.path.join(root, fname)
                        break

            if middle_json_path and os.path.exists(middle_json_path):
                logger.info(f"[MinerU] Found middle_json at: {middle_json_path}")
                with open(middle_json_path, "r", encoding="utf-8") as f:
                    middle_json = json_module.load(f)
                logger.info(f"[MinerU] middle_json loaded with keys: {list(middle_json.keys())}")
            else:
                logger.warning(f"[MinerU] No _middle.json file found in ZIP")
                # 列出解压目录内容以便调试
                for root, dirs, files in os.walk(extract_dir):
                    for fname in files:
                        logger.info(f"[MinerU] ZIP file: {os.path.join(root, fname)}")

            # 清理临时文件
            try:
                os.unlink(zip_path)
            except Exception:
                pass

            processing_time = time.time() - start_time
            logger.info(f"[MinerU] Parse completed in {processing_time:.2f}s")

            return middle_json, result

        except httpx.HTTPError as e:
            import traceback
            logger.error(f"[MinerU] HTTP error: {traceback.format_exc()}")
            raise ParsingError(f"Failed to parse {file_path}: {e}")
        except Exception as e:
            import traceback
            logger.error(f"[MinerU] Parse failed: {traceback.format_exc()}")
            raise ParsingError(f"Failed to parse {file_path}: {e}")

    def _build_form_data(
        self,
        backend: str,
        return_middle_json: bool,
        return_model_output: bool,
        start_page_id: int = 0,
        end_page_id: int = 99999,
        **kwargs,
    ) -> dict:
        """Build form data for API request."""
        return {
            "lang_list": kwargs.get("lang", "ch"),
            "backend": backend,
            "parse_method": kwargs.get("parse_method", "auto"),
            "formula_enable": str(kwargs.get("formula_enable", True)).lower(),
            "table_enable": str(kwargs.get("table_enable", True)).lower(),
            "image_analysis": str(kwargs.get("image_analysis", True)).lower(),
            "return_md": "true",
            "return_middle_json": str(return_middle_json).lower(),
            "return_model_output": str(return_model_output).lower(),
            "return_content_list": "true",
            "return_images": "true",
            "response_format_zip": "true",  # 返回 ZIP 格式
            "return_original_file": "false",
            "start_page_id": str(start_page_id),
            "end_page_id": str(end_page_id),
            "server_url": "string",
        }

    async def parse_pages(
        self,
        file_path: str,
        page_backend_ranges: list[tuple[int, int, str]],
        return_middle_json: bool = True,
        return_model_output: bool = False,
        **kwargs,
    ) -> tuple[dict, Any]:
        """Parse document with different backends for different page ranges.

        Args:
            file_path: Path to the file to parse
            page_backend_ranges: List of (start_page, end_page, backend) tuples.
                Each range is parsed with its assigned backend, then results are merged.
            return_middle_json: Whether to return middle_json
            return_model_output: Whether to return model output
            **kwargs: Additional options passed to each parse call

        Returns:
            Merged middle_json from all page ranges
        """
        import asyncio

        logger.info(f"[MinerU] parse_pages called with {len(page_backend_ranges)} ranges")

        async def parse_range(start: int, end: int, backend: str) -> dict:
            logger.info(f"[MinerU] Parsing range {start}-{end} with backend={backend}")
            middle_json, _ = await self.parse(
                file_path=file_path,
                backend=backend,
                return_middle_json=return_middle_json,
                return_model_output=False,
                start_page_id=start,
                end_page_id=end,
                **kwargs,
            )
            return middle_json

        tasks = [
            parse_range(start, end, backend)
            for start, end, backend in page_backend_ranges
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter out exceptions and log them
        valid_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"[MinerU] Range {page_backend_ranges[i]} failed: {result}")
            else:
                valid_results.append(result)

        if not valid_results:
            raise ParsingError(f"All page ranges failed for {file_path}")

        # Merge results
        merged = self._merge_middle_json_list(valid_results)
        return merged, {}

    def _merge_middle_json_list(self, results: list[dict]) -> dict:
        """Merge multiple middle_json results by page index."""
        if len(results) == 1:
            return results[0]

        page_map = {}
        for result in results:
            backend = result.get("_backend", "unknown")
            for page in result.get("pdf_info", []):
                page_idx = page.get("page_idx")
                if page_idx not in page_map:
                    page_map[page_idx] = []
                page_map[page_idx].append({"page": page, "backend": backend})

        merged_pages = []
        for page_idx in sorted(page_map.keys()):
            versions = page_map[page_idx]
            if len(versions) == 1:
                page = versions[0]["page"]
                page["_merged_from"] = 1
                merged_pages.append(page)
            else:
                # Select page with most blocks (best quality)
                best = max(versions, key=lambda v: len(v["page"].get("preproc_blocks", [])))
                page = best["page"]
                page["_merged_from"] = len(versions)
                merged_pages.append(page)

        merged = results[0].copy()
        merged["pdf_info"] = merged_pages
        merged["_backend"] = "merged"
        merged["_page_count"] = len(merged_pages)

        return merged

    async def close(self) -> None:
        """Close the client."""
        pass