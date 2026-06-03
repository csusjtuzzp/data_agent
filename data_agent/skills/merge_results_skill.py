# Copyright (c) Data Agent Team. All rights reserved.
"""MergeResultsSkill - 合并多后端解析结果"""

from typing import Any, List

from loguru import logger

from data_agent.skills.base_skill import BaseSkill, SkillConfig


class MergeResultsSkill(BaseSkill):
    """合并多次解析的结果，优先选择高质量页面"""

    def __init__(self, config: SkillConfig):
        super().__init__(config)
        self.prefer_backend = config.parameters.get("prefer_backend", None)
        self.merge_strategy = config.parameters.get("merge_strategy", "prefer_more_blocks")

    async def execute(self, results_list: List[dict] = None, **kwargs) -> dict:
        """合并多个解析结果"""
        if not results_list or len(results_list) <= 1:
            logger.info("[MergeResults] Single or empty result list, returning first")
            return results_list[0] if results_list else {}

        logger.info(f"[MergeResults] Merging {len(results_list)} results")

        # 按页面合并
        merged = self._merge_by_page(results_list)

        merged["_merged"] = True
        merged["_result_count"] = len(results_list)
        merged["_merge_strategy"] = self.merge_strategy

        logger.info("[MergeResults] Merge completed")
        return merged

    def validate_input(self, data: Any) -> bool:
        """验证输入"""
        if data is None:
            return True
        if isinstance(data, list):
            return all(isinstance(r, dict) for r in data)
        return isinstance(data, dict)

    def _merge_by_page(self, results_list: List[dict]) -> dict:
        """按页面合并结果"""
        # 收集所有页面的页码
        all_page_indices = set()
        for result in results_list:
            for page in result.get("pdf_info", []):
                all_page_indices.add(page.get("page_idx"))

        merged_pages = []
        for page_idx in sorted(all_page_indices):
            # 收集该页面在所有结果中的版本
            page_versions = []
            for result in results_list:
                for page in result.get("pdf_info", []):
                    if page.get("page_idx") == page_idx:
                        page_versions.append(page)
                        break

            # 合并该页面
            merged_page = self._merge_single_page(page_versions)
            merged_pages.append(merged_page)

        # 构建合并结果
        merged = results_list[0].copy() if results_list else {}
        merged["pdf_info"] = merged_pages
        merged["_backend"] = "merged"

        return merged

    def _merge_single_page(self, page_versions: List[dict]) -> dict:
        """合并单个页面的多个版本"""
        if len(page_versions) == 1:
            return page_versions[0]

        # 选择最佳版本
        best_page = page_versions[0]
        best_score = self._score_page(page_versions[0])

        for version in page_versions[1:]:
            score = self._score_page(version)
            if score > best_score:
                best_page = version
                best_score = score

        best_page["_merged_from"] = len(page_versions)
        best_page["_original_backend"] = best_page.get("_backend")

        return best_page

    def _score_page(self, page: dict) -> float:
        """对页面质量评分"""
        score = 0.0

        # 块数量得分
        blocks = page.get("preproc_blocks", [])
        score += len(blocks) * 1.0

        # 有意义的块数量
        meaningful_blocks = [
            b for b in blocks
            if b.get("type") not in ["page_number", "header"]
        ]
        score += len(meaningful_blocks) * 2.0

        # 文本内容长度
        text_len = sum(
            len(b.get("text", ""))
            for b in blocks
            if b.get("type") == "text"
        )
        score += text_len * 0.01

        # 表格存在加分
        has_table = any(b.get("type") == "table" for b in blocks)
        if has_table:
            score += 10.0

        return score


class MergeResultsSkillV2(BaseSkill):
    """合并多后端结果 - V2 版本支持更多策略"""

    def __init__(self, config: SkillConfig):
        super().__init__(config)
        self.confidence_threshold = config.parameters.get("confidence_threshold", 0.5)

    async def execute(self, results_list: List[dict] = None, **kwargs) -> dict:
        """合并多个解析结果"""
        if not results_list or len(results_list) <= 1:
            return results_list[0] if results_list else {}

        # 按页面合并
        merged = self._merge_pages(results_list)

        merged["_merged"] = True
        merged["_merge_version"] = "v2"

        return merged

    def validate_input(self, data: Any) -> bool:
        if data is None:
            return True
        if isinstance(data, list):
            return all(isinstance(r, dict) for r in data)
        return isinstance(data, dict)

    def _merge_pages(self, results_list: List[dict]) -> dict:
        """按页面合并，使用置信度过滤"""
        page_map = {}

        for result in results_list:
            backend = result.get("_backend", "unknown")
            for page in result.get("pdf_info", []):
                page_idx = page.get("page_idx")
                if page_idx not in page_map:
                    page_map[page_idx] = []
                page_map[page_idx].append({
                    "page": page,
                    "backend": backend,
                })

        merged_pages = []
        for page_idx in sorted(page_map.keys()):
            versions = page_map[page_idx]
            merged_page = self._select_best_page(versions)
            merged_pages.append(merged_page)

        merged = results_list[0].copy() if results_list else {}
        merged["pdf_info"] = merged_pages
        merged["_backend"] = "merged"

        return merged

    def _select_best_page(self, versions: List[dict]) -> dict:
        """选择最佳页面版本"""
        if len(versions) == 1:
            page = versions[0]["page"]
            page["_merged_from"] = 1
            return page

        # 计算每个版本的得分
        scored = []
        for v in versions:
            page = v["page"]
            score = self._calculate_page_score(page)
            scored.append((score, v))

        # 选择得分最高的
        scored.sort(key=lambda x: x[0], reverse=True)
        best = scored[0][1]["page"]
        best["_merged_from"] = len(versions)
        best["_original_backend"] = versions[0]["backend"]

        return best

    def _calculate_page_score(self, page: dict) -> float:
        """计算页面得分"""
        score = 0.0

        blocks = page.get("preproc_blocks", [])
        score += len(blocks) * 2

        # 内容丰富度
        text_blocks = [b for b in blocks if b.get("type") == "text"]
        text_len = sum(len(b.get("text", "")) for b in text_blocks)
        score += min(text_len / 100, 20)

        # 表格加分
        if any(b.get("type") == "table" for b in blocks):
            score += 15

        # 置信度
        avg_confidence = sum(
            b.get("confidence", 1.0) for b in blocks
        ) / max(len(blocks), 1)
        score += avg_confidence * 10

        return score