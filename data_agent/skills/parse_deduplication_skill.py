# Copyright (c) Data Agent Team. All rights reserved.
"""ParseDeduplicationSkill - 文件解析去重，基于文件内容哈希缓存历史解析结果"""

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from data_agent.skills.base_skill import BaseSkill, SkillConfig


class ParseDeduplicationSkill(BaseSkill):
    """检查文件是否曾经解析过，如果有缓存则返回缓存的 middle_json"""

    def __init__(self, config: SkillConfig):
        super().__init__(config)
        self.cache_dir = config.parameters.get("cache_dir", "/tmp/mineru_parse_cache")
        self._ensure_cache_dir()

    def _ensure_cache_dir(self) -> None:
        """Ensure cache directory exists"""
        Path(self.cache_dir).mkdir(parents=True, exist_ok=True)

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
            logger.error(f"[ParseDeduplication] Failed to compute hash for {file_path}: {traceback.format_exc()}")
            return None

    def _get_cache_path(self, file_hash: str) -> tuple[str, str]:
        """Get paths for middle.json and meta.json"""
        cache_subdir = os.path.join(self.cache_dir, file_hash)
        middle_json_path = os.path.join(cache_subdir, "middle.json")
        meta_json_path = os.path.join(cache_subdir, "meta.json")
        return middle_json_path, meta_json_path

    async def execute(
        self,
        file_path: str = None,
        file_hash: str = None,
        store: bool = False,
        middle_json: dict = None,
        metadata: dict = None,
        **kwargs
    ) -> dict:
        """Check cache or store parse result.

        Args:
            file_path: Path to file to check/store
            file_hash: Pre-computed file hash (optional)
            store: If True, store the middle_json in cache
            middle_json: middle_json to store (required if store=True)
            metadata: Additional metadata to store with the result

        Returns:
            dict with keys:
                - cached: bool
                - middle_json: dict or None
                - cache_key: str (file hash) or None
                - metadata: dict or None
                - cache_path: str or None
        """
        # Compute hash if not provided
        if not file_hash and file_path:
            file_hash = self._compute_file_hash(file_path)

        if not file_hash:
            return {
                "cached": False,
                "middle_json": None,
                "cache_key": None,
                "metadata": None,
                "cache_path": None,
            }

        middle_json_path, meta_json_path = self._get_cache_path(file_hash)

        # Store mode: save middle_json and metadata
        if store and middle_json:
            try:
                cache_subdir = os.path.join(self.cache_dir, file_hash)
                Path(cache_subdir).mkdir(parents=True, exist_ok=True)

                # Store middle_json
                with open(middle_json_path, "w", encoding="utf-8") as f:
                    json.dump(middle_json, f, ensure_ascii=False)

                # Store metadata
                meta = metadata or {}
                meta["file_hash"] = file_hash
                meta["file_path"] = file_path
                meta["cache_time"] = str(Path(cache_subdir).stat().st_mtime if os.path.exists(cache_subdir) else 0)
                with open(meta_json_path, "w", encoding="utf-8") as f:
                    json.dump(meta, f, ensure_ascii=False)

                logger.info(f"[ParseDeduplication] Cached result for {file_hash[:16]}...")

                return {
                    "cached": False,
                    "stored": True,
                    "cache_key": file_hash,
                    "cache_path": cache_subdir,
                }

            except Exception as e:
                import traceback
                logger.error(f"[ParseDeduplication] Failed to store cache: {traceback.format_exc()}")
                return {
                    "cached": False,
                    "stored": False,
                    "cache_key": file_hash,
                    "error": str(e),
                }

        # Check mode: look for cached result
        if os.path.exists(middle_json_path):
            try:
                with open(middle_json_path, "r", encoding="utf-8") as f:
                    cached_middle_json = json.load(f)

                cached_metadata = None
                if os.path.exists(meta_json_path):
                    with open(meta_json_path, "r", encoding="utf-8") as f:
                        cached_metadata = json.load(f)

                logger.info(f"[ParseDeduplication] Cache hit for {file_hash[:16]}...")

                return {
                    "cached": True,
                    "middle_json": cached_middle_json,
                    "cache_key": file_hash,
                    "metadata": cached_metadata,
                    "cache_path": os.path.dirname(middle_json_path),
                }

            except Exception as e:
                import traceback
                logger.error(f"[ParseDeduplication] Failed to load cache: {traceback.format_exc()}")
                return {
                    "cached": False,
                    "middle_json": None,
                    "cache_key": file_hash,
                    "error": str(e),
                }

        # No cache found
        logger.info(f"[ParseDeduplication] Cache miss for {file_hash[:16]}...")
        return {
            "cached": False,
            "middle_json": None,
            "cache_key": file_hash,
            "metadata": None,
            "cache_path": None,
        }

    def validate_input(self, data: Any) -> bool:
        """Validate input data"""
        if data is None:
            return True
        if isinstance(data, dict):
            return "file_path" in data or "file_hash" in data
        return False