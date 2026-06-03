# Copyright (c) Data Agent Team. All rights reserved.
"""RecoveryExecutor sub-agent - executes recovery based on quality validation results."""

from typing import TYPE_CHECKING, Any, Optional

from loguru import logger

from data_agent.agent.base import AgentContext, AgentResponse, AgentStatus
from data_agent.agent.sub_agents.base_sub_agent import BaseSubAgent
from data_agent.agent.sub_agents.quality_validator import QualityValidator

if TYPE_CHECKING:
    from data_agent.integration.mineru_client import MinerUClient
    from data_agent.skills.registry import SkillRegistry


class RecoveryExecutor(BaseSubAgent):
    """Executes recovery strategies based on quality validation results.

    Strategies:
        - full_retry_with_merge: re-parse problem pages with new backend, merge into original
        - partial_pass_with_warning: keep good pages, mark problem pages
        - no_recovery_needed: return original middle_json unchanged

    After recovery, re-validates to confirm issues are resolved.
    """

    def __init__(
        self,
        skill_registry: Optional["SkillRegistry"] = None,
        mineru_client: Optional["MinerUClient"] = None,
    ):
        super().__init__("RecoveryExecutor", skill_registry)
        self.mineru_client = mineru_client
        self._quality_validator = QualityValidator(skill_registry)

    async def execute(self, context: AgentContext) -> AgentResponse:
        await self.pre_execute(context)

        input_data = context.original_input

        validation_result = input_data.get("validation_result")
        middle_json = input_data.get("middle_json")
        file_path = input_data.get("file_path")
        backend = input_data.get("backend", "unknown")

        if not validation_result:
            logger.warning("[RecoveryExecutor] No validation_result provided")
            return AgentResponse(
                success=False,
                status=AgentStatus.FAILED,
                error="validation_result is required",
                context=context,
            )

        if not middle_json:
            logger.warning("[RecoveryExecutor] No middle_json provided")
            return AgentResponse(
                success=False,
                status=AgentStatus.FAILED,
                error="middle_json is required",
                context=context,
            )

        task_id = context.task_id or "unknown"

        try:
            logger.info(f"[RecoveryExecutor] Starting recovery for task {task_id}")
            logger.info(f"[RecoveryExecutor] Backend: {backend}, passed: {validation_result.get('passed', False)}, score: {validation_result.get('score', 0):.2f}")

            # Generate recovery suggestion
            suggestion = self._generate_recovery_suggestion(validation_result, backend)
            logger.info(f"[RecoveryExecutor] Strategy: {suggestion.strategy}, problem_pages: {suggestion.problem_pages}")

            # Execute strategy
            recovered_middle_json = middle_json
            recovery_succeeded = False

            if suggestion.strategy == "full_retry_with_merge":
                recovered_middle_json, recovery_succeeded = await self._execute_full_retry_with_merge(
                    suggestion=suggestion,
                    file_path=file_path,
                    current_middle_json=middle_json,
                    context=context,
                )
            elif suggestion.strategy == "partial_pass_with_warning":
                recovered_middle_json = self._execute_partial_pass_with_warning(suggestion, middle_json)
                recovery_succeeded = True
            elif suggestion.strategy == "no_recovery_needed":
                logger.info("[RecoveryExecutor] No recovery needed, validation passed")
                recovered_middle_json = middle_json
                recovery_succeeded = True
            else:
                logger.warning(f"[RecoveryExecutor] Unknown strategy: {suggestion.strategy}")
                recovery_succeeded = False

            # Re-validate after recovery
            revalidation_result = await self._revalidate(recovered_middle_json, context)

            logger.info(
                f"[RecoveryExecutor] Recovery complete: strategy={suggestion.strategy}, "
                f"succeeded={recovery_succeeded}, revalidation_passed={revalidation_result.get('passed', False)}, "
                f"revalidation_score={revalidation_result.get('score', 0):.2f}"
            )

            output = {
                "middle_json": recovered_middle_json,
                "recovery_strategy": suggestion.strategy,
                "problem_pages_recovered": suggestion.problem_pages,
                "recovery_succeeded": recovery_succeeded,
                "revalidation_passed": revalidation_result.get("passed", False),
                "revalidation_score": revalidation_result.get("score", 0),
                "revalidation_issues": revalidation_result.get("issues", []),
                "revalidation_grade": revalidation_result.get("grade", "D"),
            }

            return AgentResponse(
                success=True,
                status=AgentStatus.COMPLETED,
                output=output,
                context=context,
            )

        except Exception as e:
            import traceback
            logger.error(f"[RecoveryExecutor] Recovery failed: {traceback.format_exc()}")
            return AgentResponse(
                success=False,
                status=AgentStatus.FAILED,
                error=str(e),
                context=context,
            )

    def _generate_recovery_suggestion(self, validation_result: dict, current_backend: str) -> Any:
        """Generate recovery suggestion from validation result."""
        # Import here to avoid circular dependency
        from data_agent.agent.sub_agents.quality_validator import Issue, RecoverySuggestion

        passed = validation_result.get("passed", False)
        issues = validation_result.get("issues", [])
        summary = validation_result.get("summary", {})

        # Convert issues to proper format with level preserved
        issue_objects = []
        for i in issues:
            if isinstance(i, dict):
                issue_objects.append(Issue(
                    level=i.get("level", "warning"),
                    page=i.get("page", -1),
                    block_index=i.get("block_index", -1),
                    type=i.get("type", ""),
                    detail=i.get("detail", ""),
                    suggestion=i.get("suggestion", ""),
                ))
            else:
                issue_objects.append(i)

        # Use QualityValidator's method - passes both warning and critical issues
        return self._quality_validator.generate_recovery_suggestion(
            issues=issue_objects,
            summary=self._validation_summary_from_dict(summary),
            current_backend=current_backend,
        )

    def _validation_summary_from_dict(self, summary: dict) -> Any:
        """Convert dict to ValidationSummary."""
        from data_agent.agent.sub_agents.quality_validator import ValidationSummary
        return ValidationSummary(
            total_pages=summary.get("total_pages", 0),
            total_blocks=summary.get("total_blocks", 0),
            block_types=summary.get("block_types", {}),
            empty_pages=summary.get("empty_pages", 0),
            discarded_ratio=summary.get("discarded_ratio", 0.0),
        )

    async def _execute_full_retry_with_merge(
        self,
        suggestion: Any,
        file_path: str,
        current_middle_json: dict,
        context: AgentContext,
    ) -> tuple[dict, bool]:
        """Re-parse problem pages with new backend, merge into original."""
        from data_agent.skills.merge_results_skill import MergeResultsSkill
        from data_agent.skills.base_skill import SkillConfig

        if not file_path:
            logger.warning("[RecoveryExecutor] No file_path for retry")
            return current_middle_json, False

        next_backend = suggestion.next_backend or "pipeline"
        problem_pages = suggestion.problem_pages

        logger.info(f"[RecoveryExecutor] Retrying {len(problem_pages)} pages with backend: {next_backend}")

        try:
            # Full re-parse with new backend
            new_result, _ = await self.mineru_client.parse(
                file_path=file_path,
                backend=next_backend,
                return_middle_json=True,
            )

            if not new_result:
                logger.warning("[RecoveryExecutor] Retry returned empty result")
                return current_middle_json, False

            logger.info(f"[RecoveryExecutor] Retry parse completed, pages: {len(new_result.get('pdf_info', []))}")

            # Merge results - replace problem pages with new versions, keep rest from original
            merged = self._merge_pages(current_middle_json, new_result, problem_pages)

            logger.info("[RecoveryExecutor] Merge completed")
            return merged, True

        except Exception as e:
            import traceback
            logger.error(f"[RecoveryExecutor] Retry failed: {traceback.format_exc()}")
            # Fallback to partial pass - keep good pages from original
            fallback = self._execute_partial_pass_with_warning(suggestion, current_middle_json)
            return fallback, False

    def _merge_pages(
        self,
        original: dict,
        new_result: dict,
        problem_pages: list,
    ) -> dict:
        """Merge new result into original, replacing only problem pages."""
        original_pages = {p.get("page_idx"): p for p in original.get("pdf_info", [])}
        new_pages = {p.get("page_idx"): p for p in new_result.get("pdf_info", [])}

        merged_pages = []
        all_page_indices = set(original_pages.keys()) | set(new_pages.keys())

        for page_idx in sorted(all_page_indices):
            if page_idx in problem_pages and page_idx in new_pages:
                # Use new version for problem pages
                merged_pages.append(new_pages[page_idx])
            elif page_idx in original_pages:
                # Keep original version for good pages
                merged_pages.append(original_pages[page_idx])
            elif page_idx in new_pages:
                # Include new pages that weren't in original
                merged_pages.append(new_pages[page_idx])

        merged = original.copy()
        merged["pdf_info"] = merged_pages
        merged["_backend"] = f"{original.get('_backend', 'unknown')}_retry"
        merged["_recovered"] = True
        merged["_retry_problem_pages"] = problem_pages

        return merged

    def _execute_partial_pass_with_warning(self, suggestion: Any, current_middle_json: dict) -> dict:
        """Keep good pages, mark problem pages with recovered flag."""
        problem_pages = set(suggestion.problem_pages)
        good_pages = suggestion.good_pages

        logger.info(f"[RecoveryExecutor] Partial pass: {len(good_pages)} good pages, {len(problem_pages)} problem pages marked")

        # Mark problem pages with flags
        marked_pages = []
        for page in current_middle_json.get("pdf_info", []):
            page_idx = page.get("page_idx", -1)
            if page_idx in problem_pages:
                page = page.copy()
                page["_recovered_flag"] = True
                page["_recovered_reason"] = "quality_issue"
            marked_pages.append(page)

        result = current_middle_json.copy()
        result["pdf_info"] = marked_pages
        result["_recovery_strategy"] = "partial_pass_with_warning"
        result["_good_pages_count"] = len(good_pages)
        result["_problem_pages_count"] = len(problem_pages)

        return result

    async def _revalidate(self, middle_json: dict, context: AgentContext) -> dict:
        """Re-run quality validation on recovered middle_json."""
        try:
            # Create validation context
            val_context = AgentContext(
                task_id=context.task_id,
                original_input={"middle_json": middle_json},
                current_state=context.current_state.copy(),
                metadata={"revalidation": True},
            )

            # Run validation
            result = await self._quality_validator._validate(middle_json, task_id=context.task_id or "unknown")

            return {
                "passed": result.passed,
                "score": result.score,
                "grade": result.grade,
                "issues": [i.to_dict() if hasattr(i, "to_dict") else i for i in result.issues],
                "summary": {
                    "total_pages": result.summary.total_pages,
                    "total_blocks": result.summary.total_blocks,
                    "empty_pages": result.summary.empty_pages,
                    "discarded_ratio": result.summary.discarded_ratio,
                },
            }
        except Exception as e:
            import traceback
            logger.error(f"[RecoveryExecutor] Re-validation failed: {traceback.format_exc()}")
            return {
                "passed": False,
                "score": 0.0,
                "grade": "D",
                "issues": [{"level": "critical", "type": "revalidation_error", "detail": str(e)}],
            }