# Copyright (c) Data Agent Team. All rights reserved.
"""MinerUParseSkill - Actual document parsing using MinerU."""

from typing import Any, Optional
import hashlib
import json
import os
from pathlib import Path

from data_agent.skills.base_skill import BaseSkill, SkillConfig
from loguru import logger


class MinerUParseSkill(BaseSkill):
    """Skill for actual document parsing using MinerU backend.

    This skill takes a file path and returns middle_json by calling MinerU.
    It differs from ParseSkill which only does post-processing.
    """

    def __init__(
        self,
        config: SkillConfig,
        mineru_client: Optional[Any] = None,
        cache_dir: str = "/tmp/mineru_parse_cache",
    ):
        super().__init__(config)
        self.mineru_client = mineru_client
        self.cache_dir = cache_dir
        self.default_backend = config.parameters.get("backend", "hybrid-auto-engine")

    async def execute(self, data: Any, **kwargs) -> Any:
        """Parse document using MinerU and return middle_json."""
        file_path = kwargs.get("path") or (data.get("path") if isinstance(data, dict) else None)

        if not file_path or file_path == "N/A":
            logger.warning("[MinerUParseSkill] No valid file path, returning input unchanged")
            return data if isinstance(data, dict) else {}

        logger.info(f"[MinerUParseSkill] Parsing file: {file_path}")

        cached_result = await self._check_cache(file_path)
        if cached_result:
            logger.info(f"[MinerUParseSkill] Cache hit for {file_path}")
            return cached_result

        if not self.mineru_client:
            logger.warning(f"[MinerUParseSkill] MinerU client not configured, returning input unchanged")
            return data if isinstance(data, dict) else {}

        try:
            backend = kwargs.get("backend", self.default_backend)
            middle_json, _ = await self.mineru_client.parse(
                file_path=file_path,
                backend=backend,
                return_middle_json=True,
                return_model_output=False,
            )

            await self._store_cache(file_path, middle_json, backend)

            return middle_json

        except Exception as e:
            import traceback
            logger.error(f"[MinerUParseSkill] Parse failed: {traceback.format_exc()}")
            raise

    def _compute_file_hash(self, file_path: str) -> Optional[str]:
        """Compute SHA256 hash of file content."""
        try:
            hasher = hashlib.sha256()
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except Exception as e:
            import traceback
            logger.error(f"[MinerUParseSkill] Hash failed: {traceback.format_exc()}")
            return None

    async def _check_cache(self, file_path: str) -> Optional[dict]:
        """Check cache for parsed result."""
        try:
            file_hash = self._compute_file_hash(file_path)
            if not file_hash:
                return None

            cache_path = os.path.join(self.cache_dir, file_hash, "middle.json")
            if os.path.exists(cache_path):
                with open(cache_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            return None
        except Exception as e:
            import traceback
            logger.error(f"[MinerUParseSkill] Cache check failed: {traceback.format_exc()}")
            return None

    async def _store_cache(self, file_path: str, middle_json: dict, backend: str) -> None:
        """Store parse result in cache."""
        try:
            file_hash = self._compute_file_hash(file_path)
            if not file_hash:
                return

            cache_dir = os.path.join(self.cache_dir, file_hash)
            Path(cache_dir).mkdir(parents=True, exist_ok=True)

            cache_path = os.path.join(cache_dir, "middle.json")
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(middle_json, f, ensure_ascii=False)

            meta_path = os.path.join(cache_dir, "meta.json")
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump({"file_hash": file_hash, "backend": backend}, f)

            logger.info(f"[MinerUParseSkill] Stored cache for {file_path}")
        except Exception as e:
            import traceback
            logger.error(f"[MinerUParseSkill] Cache store failed: {traceback.format_exc()}")

    async def _fallback_parse(self, file_path: str) -> dict:
        """Fallback parsing when MinerU client is not available."""
        logger.warning(f"[MinerUParseSkill] Using fallback parse for {file_path}")
        # Return empty middle_json instead of overwriting existing data
        return {
            "pdf_info": [],
            "_backend": "fallback",
            "_version_name": "1.0.0",
            "_error": "MinerU client not configured",
        }

    def _fallback_no_op(self, data: dict) -> dict:
        """When no MinerU client and no valid cache, return original data unchanged."""
        logger.warning("[MinerUParseSkill] No MinerU client and no cache, returning input unchanged")
        return data

    def validate_input(self, data: Any) -> bool:
        """Validate that we have a path to parse."""
        if isinstance(data, dict):
            return "path" in data or "pdf_info" in data
        return True