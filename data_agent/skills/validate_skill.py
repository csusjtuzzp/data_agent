# Copyright (c) Data Agent Team. All rights reserved.
"""ValidateSkill for validating middle_json quality."""

from typing import Any
from datetime import datetime

from data_agent.skills.base_skill import BaseSkill, SkillConfig


class ValidateSkill(BaseSkill):
    """Skill for validating middle_json quality and returning structured ValidatorOutput."""

    def __init__(self, config: SkillConfig):
        super().__init__(config)
        self.min_page_count = config.parameters.get("min_page_count", 1)
        self.min_block_count = config.parameters.get("min_block_count", 1)
        self.min_quality_score = config.parameters.get("min_quality_score", 0.5)

    async def execute(self, data: Any, **kwargs) -> Any:
        """Validate middle_json and return ValidatorOutput."""
        try:
            if not self.validate_input(data):
                return self._create_failed_output("Invalid middle_json structure")

            issues = []
            root_causes = []
            recommended_actions = []
            bad_pages = []  # Track pages with quality issues for _page_quality marker

            pdf_info = data.get("pdf_info") or []
            page_count = len(pdf_info)
            total_blocks = 0
            for p in pdf_info:
                total_blocks += len(p.get("preproc_blocks") or [])
                total_blocks += len(p.get("para_blocks") or [])

            if page_count < self.min_page_count:
                issues.append({
                    "level": "critical",
                    "page": -1,  # document-level issue
                    "issue_type": "no_pages",
                    "detail": f"Page count {page_count} is less than minimum {self.min_page_count}",
                })
                root_causes.append("no_pages_detected")
                recommended_actions.append({
                    "action_id": "reparse_document",
                    "skill_name": "parse_skill",
                    "priority": 1,
                    "target_pages": [],
                    "params": {},
                    "rationale": "Need to parse document to get pages",
                })

            empty_pages = []
            low_text_pages = []
            low_block_pages = []

            pdf_info = data.get("pdf_info", []) or []
            for i, page in enumerate(pdf_info):
                # Handle None page
                if page is None:
                    page = {}
                # Normalize page to dict if it's a Pydantic model
                elif hasattr(page, "model_dump"):
                    page = page.model_dump()

                preproc_blocks = page.get("preproc_blocks") or []
                para_blocks = page.get("para_blocks") or []

                # Normalize para_blocks too (for office formats)
                normalized_para = []
                for b in para_blocks:
                    if isinstance(b, dict):
                        # Ensure lines is not None
                        if "lines" in b and b["lines"] is None:
                            b = dict(b)
                            b["lines"] = []
                        normalized_para.append(b)
                    elif hasattr(b, "model_dump"):
                        bd = b.model_dump()
                        if bd.get("lines") is None:
                            bd["lines"] = []
                        normalized_para.append(bd)
                para_blocks = normalized_para

                # Ensure all blocks have proper types - convert any non-serializable types
                normalized_preproc = []
                for b in preproc_blocks:
                    if isinstance(b, dict):
                        # Ensure type is string (convert enums/other types)
                        bt = b.get("type")
                        if hasattr(bt, "value"):
                            b = dict(b)
                            b["type"] = bt.value
                        normalized_preproc.append(b)
                    elif hasattr(b, "model_dump"):
                        # It's a Pydantic model - dump it
                        bd = b.model_dump()
                        bt = bd.get("type")
                        if hasattr(bt, "value"):
                            bd["type"] = bt.value
                        normalized_preproc.append(bd)
                    else:
                        normalized_preproc.append({"type": str(type(b).__name__)})
                preproc_blocks = normalized_preproc
                block_types_set = {b.get("type") for b in preproc_blocks}

                # Check empty page - considers both preproc_blocks and para_blocks
                has_preproc_content = bool(preproc_blocks)
                has_para_content = bool(para_blocks)
                is_empty = not has_preproc_content and not has_para_content

                if is_empty:
                    empty_pages.append(i)
                    issues.append({
                        "level": "warning",
                        "page": i,
                        "issue_type": "empty_page",
                        "detail": f"Page {i} has no content blocks",
                    })
                    bad_pages.append(i)

                # Check low text content - check both preproc and para blocks
                text_len = 0
                # From preproc_blocks
                # text_len += sum(len(b.get("text", "")) for b in preproc_blocks if b.get("type") in ("text", "title", "list"))
                for b in preproc_blocks:
                    if b.get("type") in ("text", "title", "list"):
                        b_text = b.get("text", "")
                        if b_text:
                            text_len += len(b.get("text", ""))
                # text_len += sum(len(b.get("text", "")) for b in preproc_blocks if b.get("type") in ("text", "title", "list"))                # From para_blocks (office format)
                for pb in para_blocks:
                    if pb.get("type") in ("text", "title"):
                        for line in pb.get("lines", []):
                            if isinstance(line, dict):
                                for span in line.get("spans", []):
                                    content = span.get("content", "")
                                    if content:
                                        text_len += len(content)

                if (has_preproc_content or has_para_content) and text_len < 50:
                    low_text_pages.append(i)
                    issues.append({
                        "level": "warning",
                        "page": i,
                        "issue_type": "low_text_content",
                        "detail": f"Page {i} has low text content ({text_len} chars)",
                    })
                    if i not in bad_pages:
                        bad_pages.append(i)

                # Check low block count (meaningful blocks only)
                content_blocks = [b for b in preproc_blocks if b.get("type") not in ("page_number", "header", "footer", "separator")]
                if (preproc_blocks or para_blocks) and len(content_blocks) + len(para_blocks) < 3:
                    low_block_pages.append(i)
                    issues.append({
                        "level": "info",
                        "page": i,  # Use actual page index
                        "issue_type": "low_block_count",
                        "detail": f"Page {i} has only {len(content_blocks)} content blocks",
                    })

            if empty_pages:
                root_causes.append("empty_page")
                recommended_actions.append({
                    "action_id": "remove_empty_pages",
                    "skill_name": "remove_empty_pages_skill",
                    "priority": 2,
                    "target_pages": empty_pages,
                    "params": {},
                    "rationale": f"Remove {len(empty_pages)} empty pages from output",
                })

            if low_text_pages:
                root_causes.append("low_text_content")
                recommended_actions.append({
                    "action_id": "reparse_bad_pages",
                    "skill_name": "reparse_bad_pages_skill",
                    "priority": 1,
                    "target_pages": low_text_pages,
                    "params": {},
                    "rationale": f"Re-parse {len(low_text_pages)} pages with low text content",
                })

            total_content_blocks = sum(len(p.get("preproc_blocks") or []) + len(p.get("para_blocks") or []) for p in pdf_info)
            if total_content_blocks < self.min_block_count:
                issues.append({
                    "level": "critical",
                    "page": -1,
                    "issue_type": "low_block_count",
                    "detail": f"Total block count {total_content_blocks} is less than minimum {self.min_block_count}",
                })
                if "low_text_content" not in root_causes:
                    root_causes.append("low_text_content")

            block_types = {}
            for page in data.get("pdf_info") or []:
                for block in page.get("preproc_blocks", []):
                    block_type = block.get("type", "unknown")
                    # Convert enum to string for JSON serializability
                    if hasattr(block_type, 'value'):
                        block_type = block_type.value
                    elif not isinstance(block_type, str):
                        block_type = str(block_type)
                    block_types[block_type] = block_types.get(block_type, 0) + 1

            discarded_blocks = sum(len(p.get("discarded_blocks", [])) for p in data.get("pdf_info") or [])
            total_with_discarded = total_content_blocks + discarded_blocks
            discarded_ratio = discarded_blocks / total_with_discarded if total_with_discarded > 0 else 0

            if discarded_ratio > 0.3:
                issues.append({
                    "level": "warning",
                    "page": -1,
                    "issue_type": "high_discarded_ratio",
                    "detail": f"Discarded ratio {discarded_ratio:.2%} is high ({discarded_blocks} discarded blocks)",
                })
                root_causes.append("high_discarded_ratio")
                recommended_actions.append({
                    "action_id": "lower_threshold",
                    "skill_name": "lower_score_skill",
                    "priority": 2,
                    "target_pages": [],
                    "params": {},
                    "rationale": "Lower threshold to keep more blocks",
                })

            passed = len([i for i in issues if i["level"] == "critical"]) == 0
            score = self._calculate_score(issues, page_count, total_content_blocks)

            result = {
                "passed": passed,
                "score": score,
                "grade": self._get_grade(score),
                "root_causes": root_causes,
                "recommended_actions": recommended_actions,
                "issues": issues,
                "summary": {
                    "total_pages": page_count,
                    "total_blocks": total_content_blocks,
                    "block_types": block_types,
                    "empty_pages": len(empty_pages),
                    "low_text_pages": low_text_pages,
                    "low_block_pages": low_block_pages,
                    "bad_pages": bad_pages,
                    "discarded_ratio": discarded_ratio,
                },
                "validator_name": "ValidateSkill",
                "timestamp": datetime.utcnow().isoformat(),
            }

            # Preserve pdf_info from input in result to prevent data loss
            result["pdf_info"] = data.get("pdf_info")

            # Mark bad pages in middle_json for downstream skills
            if bad_pages:
                data["_bad_page_indices"] = bad_pages
                data["_page_quality_summary"] = {
                    "total_bad": len(bad_pages),
                    "empty_pages": len(empty_pages),
                    "low_text_pages": low_text_pages,
                }
        except Exception as e:
            import traceback
            print(traceback.format_exc())

        return result

    def validate_input(self, data: Any) -> bool:
        """Validate middle_json structure."""
        return isinstance(data, dict) and "pdf_info" in data

    def _create_failed_output(self, reason: str) -> dict:
        """Create a failed validation output."""
        return {
            "passed": False,
            "score": 0.0,
            "grade": "F",
            "root_causes": ["validation_failed"],
            "recommended_actions": [],
            "issues": [{"level": "critical", "detail": reason}],
            "summary": {},
            "validator_name": "ValidateSkill",
            "timestamp": datetime.utcnow().isoformat(),
        }

    def _calculate_score(self, issues: list, page_count: int, total_blocks: int) -> float:
        """Calculate quality score based on issues and content."""
        base_score = 1.0

        critical_count = len([i for i in issues if i["level"] == "critical"])
        warning_count = len([i for i in issues if i["level"] == "warning"])

        base_score -= critical_count * 0.3
        base_score -= warning_count * 0.1

        if page_count == 0:
            return 0.0

        density_score = min(1.0, total_blocks / (page_count * 10))
        base_score = base_score * 0.7 + density_score * 0.3

        return max(0.0, min(1.0, base_score))

    def _get_grade(self, score: float) -> str:
        """Get grade based on score."""
        if score >= 0.9:
            return "A"
        elif score >= 0.7:
            return "B"
        elif score >= 0.5:
            return "C"
        elif score >= 0.3:
            return "D"
        else:
            return "F"