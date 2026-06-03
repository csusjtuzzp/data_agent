# Copyright (c) Data Agent Team. All rights reserved.
"""Quality Validator sub-agent with comprehensive validation rules."""

import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional

from loguru import logger

from data_agent.agent.base import AgentContext, AgentResponse, AgentStatus
from data_agent.agent.sub_agents.base_sub_agent import BaseSubAgent


@dataclass
class Issue:
    """A single validation issue."""

    level: str  # critical, warning
    page: int = -1
    block_index: int = -1
    type: str = ""
    detail: str = ""
    suggestion: str = ""

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "page": self.page,
            "block_index": self.block_index,
            "type": self.type,
            "detail": self.detail,
            "suggestion": self.suggestion,
        }


@dataclass
class ValidationSummary:
    """Summary statistics of the validation."""

    total_pages: int = 0
    total_blocks: int = 0
    block_types: dict = field(default_factory=dict)
    empty_pages: int = 0
    discarded_ratio: float = 0.0
    text_pages: int = 0
    table_count: int = 0
    image_count: int = 0


@dataclass
class ValidationResult:
    """Result of validation checks with detailed structure."""

    passed: bool
    score: float
    grade: str
    issues: list
    summary: ValidationSummary
    filtered_json: dict = None
    logs: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "score": self.score,
            "grade": self.grade,
            "issues": [i.to_dict() if isinstance(i, Issue) else i for i in self.issues],
            "summary": {
                "total_pages": self.summary.total_pages,
                "total_blocks": self.summary.total_blocks,
                "block_types": self.summary.block_types,
                "empty_pages": self.summary.empty_pages,
                "discarded_ratio": self.summary.discarded_ratio,
            },
            "filtered_json": self.filtered_json,
            "logs": self.logs,
        }


@dataclass
class RecoverySuggestion:
    """Suggestion for error recovery."""

    strategy: str  # partial_retry, full_retry_with_merge, partial_pass_with_warning
    next_backend: str = None
    problem_pages: list = field(default_factory=list)
    good_pages: list = field(default_factory=list)
    suggestion: str = ""
    merge_rule: str = ""

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "next_backend": self.next_backend,
            "problem_pages": self.problem_pages,
            "good_pages": self.good_pages,
            "suggestion": self.suggestion,
            "merge_rule": self.merge_rule,
        }


class QualityValidator(BaseSubAgent):
    """Sub-agent for validating parsing quality with detailed issue reporting."""

    # Priority P0: Critical issues that must pass
    P0_RULES = ["page_count", "text_content", "backend_field"]

    # Priority P1: Important issues affecting structure quality
    P1_RULES = ["empty_pages", "table_structure", "block_count"]

    # Priority P2: Warnings that don't block but reduce quality
    P2_RULES = ["discarded_ratio", "block_distribution"]

    def __init__(self, skill_registry: Optional[Any] = None):
        super().__init__("QualityValidator", skill_registry)
        self.filter_skill = None

    async def execute(self, context: AgentContext) -> AgentResponse:
        await self.pre_execute(context)

        input_data = context.original_input
        middle_json = input_data.get("middle_json")
        model_json = input_data.get("model_json")
        md_content = input_data.get("md_content")
        file_path = input_data.get("file_path", "")
        doc_type = input_data.get("doc_type", "auto")
        rules = input_data.get("rules", self._default_rules())

        task_id = context.task_id or "unknown"

        if middle_json is None:
            logger.warning("[QualityValidator] middle_json is None, returning failure")
            return AgentResponse(
                success=False,
                status=AgentStatus.FAILED,
                error="middle_json is None",
                context=context,
            )

        try:
            logger.info(f"[QualityValidator] Starting validation for task {task_id}")
            logger.info(f"[QualityValidator] middle_json has {len(middle_json.get('pdf_info', []))} pages")

            result = await self._validate(
                middle_json=middle_json,
                model_json=model_json,
                md_content=md_content,
                file_path=file_path,
                doc_type=doc_type,
                rules=rules,
                task_id=task_id,
            )

            logger.info(f"[QualityValidator] Validation complete: passed={result.passed}, score={result.score:.2f}, grade={result.grade}, issues={len(result.issues)}")
            for issue in result.issues:
                logger.info(f"[QualityValidator] Issue: {issue.level} - {issue.type}: {issue.detail}")

            return AgentResponse(
                success=True,
                status=AgentStatus.COMPLETED,
                output=result.to_dict() if isinstance(result, ValidationResult) else result,
                context=context,
            )

        except Exception as e:
            import traceback
            logger.error(f"Quality validation failed: {traceback.format_exc()}")
            return AgentResponse(
                success=False,
                status=AgentStatus.FAILED,
                error=str(e),
                context=context,
            )

    def _default_rules(self) -> dict:
        return {
            "min_page_count": 1,
            "max_empty_pages_ratio": 0.3,
            "min_text_content_length": 50,
            "min_block_count_per_page": 3,
            "max_discarded_ratio": 0.2,
            "require_backend": True,
        }

    async def _validate(
        self,
        middle_json: dict,
        model_json: Any = None,
        md_content: str = None,
        file_path: str = "",
        doc_type: str = "auto",
        rules: dict = None,
        task_id: str = "unknown",
    ) -> ValidationResult:
        """Run validation checks on middle_json."""
        if middle_json is None:
            logger.warning("[QualityValidator] _validate received None middle_json")
            return ValidationResult(
                passed=False,
                score=0.0,
                grade="D",
                issues=[Issue(level="critical", type="missing_middle_json", detail="middle_json is None")],
                summary=ValidationSummary(),
            )

        rules = rules or self._default_rules()
        start_time = time.time()
        logs = []

        issues = []

        # P0 checks - must pass
        issues.extend(self._check_page_count(middle_json, rules))
        issues.extend(self._check_text_content(middle_json, rules))
        issues.extend(self._check_backend_field(middle_json))

        # P1 checks - important
        issues.extend(self._check_empty_pages(middle_json, rules))
        issues.extend(self._check_table_structure(middle_json))
        issues.extend(self._check_block_count(middle_json, rules))

        # P2 checks - warnings
        issues.extend(self._check_discarded_ratio(middle_json, rules))
        block_types = self._check_block_distribution(middle_json)

        # Build summary
        summary = self._build_summary(middle_json, block_types)

        # Calculate score and grade
        score = self._calculate_score(issues, summary)
        grade = self._calculate_grade(score, issues)

        # Determine passed status
        critical_issues = [i for i in issues if i.level == "critical"]
        passed = len(critical_issues) == 0 and score >= 0.5

        # Log validation process
        logs.append({
            "step": "validation_complete",
            "passed": passed,
            "score": score,
            "grade": grade,
            "issues_count": len(issues),
            "time_ms": (time.time() - start_time) * 1000,
        })

        result = ValidationResult(
            passed=passed,
            score=score,
            grade=grade,
            issues=issues,
            summary=summary,
            logs=logs,
        )

        # If validation passed and we have a filter skill, apply filtering
        if passed and self.filter_skill:
            result.filtered_json = await self.filter_skill.execute(middle_json)
        else:
            result.filtered_json = middle_json

        return result

    def _check_page_count(self, middle_json: dict, rules: dict) -> list:
        """P0: Check minimum page count."""
        pages = middle_json.get("pdf_info", [])
        min_pages = rules.get("min_page_count", 1)
        if len(pages) < min_pages:
            return [Issue(
                level="critical",
                type="insufficient_pages",
                detail=f"Only {len(pages)} pages, minimum required is {min_pages}",
                suggestion="Check if the file is corrupted or empty",
            )]
        return []

    def _check_text_content(self, middle_json: dict, rules: dict) -> list:
        """P0: Check that pages have text content.

        Checks both preproc_blocks (PDF format) and para_blocks (office format).
        """
        issues = []
        min_text_len = rules.get("min_text_content_length", 50)
        for page in middle_json.get("pdf_info", []):
            total_text_len = 0

            # Check preproc_blocks for text content (PDF format)
            blocks = page.get("preproc_blocks", [])
            text_blocks = [b for b in blocks if b.get("type") == "text"]
            total_text_len += sum(len(self._get_text_content(b)) for b in text_blocks)

            # Check para_blocks for text content (office format: docx/pptx/xlsx)
            para_blocks = page.get("para_blocks", [])
            for pblock in para_blocks:
                # para_blocks can contain text, title, table, image types
                block_type = pblock.get("type", "")
                if block_type in ["text", "title"]:
                    # Try to extract text from lines
                    lines = pblock.get("lines", [])
                    for line in lines:
                        if isinstance(line, dict):
                            spans = line.get("spans", [])
                            for span in spans:
                                content = span.get("content", "")
                                if content:
                                    total_text_len += len(content)
                        elif isinstance(line, list):
                            for item in line:
                                if isinstance(item, dict):
                                    content = item.get("content", "")
                                    if content:
                                        total_text_len += len(content)

            if total_text_len < min_text_len:
                issues.append(Issue(
                    level="critical",
                    page=page.get("page_idx", -1),
                    type="low_text_content",
                    detail=f"Page {page.get('page_idx', -1)} has only {total_text_len} chars of text",
                    suggestion="This may indicate an OCR failure or empty page",
                ))
        return issues

    def _check_backend_field(self, middle_json: dict) -> list:
        """P0: Check that _backend field exists."""
        if not middle_json.get("_backend"):
            return [Issue(
                level="critical",
                type="missing_backend",
                detail="Missing _backend field in middle_json",
                suggestion="The parsing backend did not set required metadata",
            )]
        return []

    def _check_empty_pages(self, middle_json: dict, rules: dict) -> list:
        """P1: Check for excessive empty pages.

        A page is considered empty if it has neither preproc_blocks (PDF format)
        nor para_blocks (office format: docx/pptx/xlsx) with meaningful content.
        """
        issues = []
        pages = middle_json.get("pdf_info", [])
        max_empty_ratio = rules.get("max_empty_pages_ratio", 0.3)
        empty_count = 0

        for page in pages:
            # Check preproc_blocks (PDF format)
            preproc_blocks = page.get("preproc_blocks", [])
            has_preproc_content = preproc_blocks and not all(
                b.get("type") in ["page_number", "header", "footer", "separator"]
                for b in preproc_blocks
            )

            # Check para_blocks (office format)
            para_blocks = page.get("para_blocks", [])
            has_para_content = bool(para_blocks)

            # Page is empty if neither format has content
            if not has_preproc_content and not has_para_content:
                empty_count += 1

        if pages:
            empty_ratio = empty_count / len(pages)
            if empty_ratio > max_empty_ratio:
                issues.append(Issue(
                    level="warning",
                    type="high_empty_ratio",
                    detail=f"{empty_count} empty pages ({empty_ratio:.1%})",
                    suggestion=f"Consider switching backend if empty pages > {max_empty_ratio:.0%}",
                ))
        return issues

    def _check_table_structure(self, middle_json: dict) -> list:
        """P1: Validate table structure using HTML analysis."""
        issues = []
        for page in middle_json.get("pdf_info", []):
            blocks = page.get("preproc_blocks", [])
            for idx, block in enumerate(blocks):
                if block.get("type") == "table":
                    score = self._validate_table_structure(block)
                    if score < 0.5:
                        issues.append(Issue(
                            level="warning",
                            page=page.get("page_idx", -1),
                            block_index=idx,
                            type="table_structure_invalid",
                            detail=f"Table at page {page.get('page_idx', -1)} block {idx} has low structure score ({score:.2f})",
                            suggestion="Try hybrid backend for better table parsing",
                        ))
        return issues

    def _validate_table_structure(self, table_block: dict) -> float:
        """Validate table structure using HTML parsing."""
        html = table_block.get("html", "")

        # Method 1: HTML structure analysis
        row_count = html.count("<tr")
        cell_count = html.count("<td") + html.count("<th")
        structure_score = min((row_count * cell_count) / 100, 1.0) if row_count > 0 and cell_count > 0 else 0

        # Method 2: Content completeness
        text_content = re.sub(r'<[^>]+>', '', html)
        content_score = min(len(text_content.strip()) / 100, 1.0) if text_content.strip() else 0

        return (structure_score + content_score) / 2

    def _check_block_count(self, middle_json: dict, rules: dict) -> list:
        """P1: Check minimum block count per page."""
        issues = []
        min_blocks = rules.get("min_block_count_per_page", 3)
        # Layout elements that don't count as meaningful content
        layout_types = {"page_number", "header", "page_footnote", "page_footer", "separator"}
        for page in middle_json.get("pdf_info", []):
            blocks = page.get("preproc_blocks", [])
            meaningful_blocks = [b for b in blocks if b.get("type") not in layout_types]
            if len(meaningful_blocks) < min_blocks:
                issues.append(Issue(
                    level="warning",
                    page=page.get("page_idx", -1),
                    type="insufficient_blocks",
                    detail=f"Page {page.get('page_idx', -1)} has only {len(meaningful_blocks)} meaningful blocks (text/table/image/figure)",
                    suggestion="Low block count may indicate parsing failure",
                ))
        return issues

    def _check_discarded_ratio(self, middle_json: dict, rules: dict) -> list:
        """P2: Check discarded blocks ratio - only flag meaningful content discards."""
        issues = []
        max_discarded_ratio = rules.get("max_discarded_ratio", 0.2)

        # Meaningful content types that indicate quality issues when discarded
        meaningful_types = {"text", "table", "image", "figure", "equation", "list"}
        # Normal discard types that don't indicate quality issues
        normal_discard_types = {"page_number", "header", "page_footnote", "page_footer", "separator"}

        for page in middle_json.get("pdf_info", []):
            discarded = page.get("discarded_blocks", [])
            preproc = page.get("preproc_blocks", [])

            # Count meaningful vs normal discards
            meaningful_discarded = sum(1 for b in discarded if b.get("type") in meaningful_types)
            normal_discarded = sum(1 for b in discarded if b.get("type") in normal_discard_types)
            total_meaningful = meaningful_discarded + len(preproc)

            # Only flag if meaningful content ratio is too low
            if total_meaningful > 0 and meaningful_discarded / total_meaningful > max_discarded_ratio:
                issues.append(Issue(
                    level="warning",
                    page=page.get("page_idx", -1),
                    type="high_discarded_ratio",
                    detail=f"Page {page.get('page_idx', -1)} has {meaningful_discarded} meaningful blocks discarded ({meaningful_discarded/total_meaningful:.1%}), {normal_discarded} normal discards (page_number/header/etc)",
                    suggestion="High meaningful discard ratio may indicate parsing issues",
                ))
        return issues

    def _check_block_distribution(self, middle_json: dict) -> dict:
        """P2: Get block type distribution."""
        type_counts = {}
        for page in middle_json.get("pdf_info", []):
            for block in page.get("preproc_blocks", []):
                block_type = block.get("type", "unknown")
                type_counts[block_type] = type_counts.get(block_type, 0) + 1
        return type_counts

    def _build_summary(self, middle_json: dict, block_types: dict) -> ValidationSummary:
        """Build validation summary."""
        pages = middle_json.get("pdf_info", [])
        total_blocks = sum(len(p.get("preproc_blocks", [])) for p in pages)
        empty_pages = sum(
            1 for p in pages
            if not p.get("preproc_blocks") or
            all(b.get("type") in ["page_number", "header"] for b in p.get("preproc_blocks", []))
        )

        total_discarded = sum(len(p.get("discarded_blocks", [])) for p in pages)
        total_all = total_discarded + total_blocks
        discarded_ratio = total_discarded / total_all if total_all > 0 else 0

        return ValidationSummary(
            total_pages=len(pages),
            total_blocks=total_blocks,
            block_types=block_types,
            empty_pages=empty_pages,
            discarded_ratio=discarded_ratio,
        )

    def _calculate_score(self, issues: list, summary: ValidationSummary) -> float:
        """Calculate validation score 0.0 - 1.0."""
        # Base score
        score = 1.0

        # Deduct for critical issues
        critical_count = sum(1 for i in issues if i.level == "critical")
        score -= critical_count * 0.25

        # Deduct for warnings
        warning_count = sum(1 for i in issues if i.level == "warning")
        score -= warning_count * 0.05

        # Penalize high empty page ratio
        if summary.total_pages > 0:
            empty_ratio = summary.empty_pages / summary.total_pages
            score -= empty_ratio * 0.15

        # Penalize high discarded ratio
        score -= summary.discarded_ratio * 0.1

        return max(0.0, min(1.0, score))

    def _calculate_grade(self, score: float, issues: list) -> str:
        """Calculate grade from score."""
        critical_count = sum(1 for i in issues if i.level == "critical")

        if critical_count > 0:
            return "D" if score < 0.3 else "C"

        if score >= 0.9:
            return "A"
        elif score >= 0.75:
            return "B"
        elif score >= 0.5:
            return "C"
        else:
            return "D"

    def _get_text_content(self, block: dict) -> str:
        """Extract text content from a block."""
        # Direct fields
        if block.get("text"):
            return block["text"]
        if block.get("content"):
            return block["content"]
        # Nested lines structure: lines[0]["spans"][0]["content"]
        lines = block.get("lines", [])
        if lines:
            first_line = lines[0]
            spans = first_line.get("spans", []) if isinstance(first_line, dict) else []
            if spans:
                first_span = spans[0]
                if isinstance(first_span, dict) and first_span.get("content"):
                    return first_span["content"]
        return ""

    def generate_recovery_suggestion(
        self,
        issues: list,
        summary: ValidationSummary,
        current_backend: str,
    ) -> RecoverySuggestion:
        """Generate recovery suggestion based on issues and current backend."""
        warning_issues = [i for i in issues if i.level == "warning"]
        critical_issues = [i for i in issues if i.level == "critical"]
        issue_types = Counter(i.type for i in warning_issues)
        problem_pages = set(i.page for i in issues if i.page >= 0)
        total_pages = summary.total_pages

        # Single page problem handling (only for warnings, not critical)
        if len(problem_pages) == 1 and total_pages > 10 and not critical_issues:
            return RecoverySuggestion(
                strategy="partial_pass_with_warning",
                problem_pages=list(problem_pages),
                good_pages=[p for p in range(total_pages) if p not in problem_pages],
                suggestion=f"Only page {list(problem_pages)[0]} has issues. Returning result for other {total_pages - 1} pages.",
            )

        # Critical issues OR multi-page problems - switch backend
        if critical_issues or issue_types:
            next_backend = self._get_next_backend(current_backend, issue_types)
            strategy = "full_retry_with_merge"
            if critical_issues and not issue_types:
                strategy = "full_retry_with_merge"  # critical issues alone trigger retry
            return RecoverySuggestion(
                strategy=strategy,
                next_backend=next_backend,
                problem_pages=list(problem_pages),
                good_pages=[p for p in range(total_pages) if p not in problem_pages],
                suggestion=f"Switch from {current_backend} to {next_backend} due to {len(critical_issues)} critical and {len(warning_issues)} warning issues",
                merge_rule="prefer_new_on_conflict",
            )

        return RecoverySuggestion(
            strategy="no_recovery_needed",
            suggestion="Validation passed, no recovery needed",
        )

    def _get_next_backend(self, current_backend: str, issue_types: Counter) -> str:
        """Determine next backend based on issue types."""
        # Decision tree based on issue types
        if "table_structure_invalid" in issue_types:
            return "hybrid"

        if "low_text_content" in issue_types:
            return "pipeline"

        if "high_empty_ratio" in issue_types or "empty_page" in issue_types:
            if current_backend == "hybrid":
                return "pipeline"
            elif current_backend == "pipeline":
                return "vlm-auto-engine"

        if "image_quality" in issue_types:
            return "vlm-auto-engine"

        # Default fallback
        if current_backend == "pipeline":
            return "hybrid"
        elif current_backend == "hybrid":
            return "vlm-auto-engine"
        else:
            return "pipeline"