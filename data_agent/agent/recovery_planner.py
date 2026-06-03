"""
RecoveryPlanner - Autonomous recovery planning from validator output.
"""

from typing import Optional, TYPE_CHECKING
from loguru import logger

if TYPE_CHECKING:
    from data_agent.state.runtime_state import RuntimeState, RecoveryPlan, RecoveryAction


class RecoveryPlanner:
    """
    Autonomous recovery planning from validator output.

    Takes ValidatorOutput (root_causes + recommended_actions) and creates
    executable recovery plans with prioritization.
    """

    ROOT_CAUSE_HANDLERS = {
        "table_structure_invalid": {
            "primary": ("repair_table_skill", "Repair table structure"),
            "fallback": ("switch_backend_skill", "Switch to VLM backend"),
        },
        "low_text_content": {
            "primary": ("retry_parse_skill", "Retry parsing with different backend"),
            "fallback": ("extract_text_skill", "Extract text using OCR"),
        },
        "empty_page": {
            "primary": ("remove_empty_pages_skill", "Remove empty pages"),
            "fallback": None,
        },
        "high_discarded_ratio": {
            "primary": ("lower_threshold_skill", "Lower threshold for keeping blocks"),
            "fallback": ("reparse_skill", "Reparse with lower confidence threshold"),
        },
        "missing_backend": {
            "primary": ("set_backend_skill", "Set backend metadata"),
            "fallback": None,
        },
        "ocr_quality_low": {
            "primary": ("retry_ocr_skill", "Retry OCR with higher quality settings"),
            "fallback": ("switch_ocr_backend_skill", "Switch OCR backend"),
        },
        "image_extraction_failed": {
            "primary": ("retry_image_extract_skill", "Retry image extraction"),
            "fallback": ("skip_images_skill", "Skip image extraction"),
        },
    }

    async def create_recovery_plan(
        self,
        failed_action: str,
        error: Optional[str],
        current_state: "RuntimeState",
    ) -> "RecoveryPlan":
        """Create a recovery plan based on current state."""

        from data_agent.state.runtime_state import RecoveryPlan, RecoveryAction

        root_cause = self._classify_failure(failed_action, error, current_state)

        handler = self.ROOT_CAUSE_HANDLERS.get(root_cause, {})

        recommended_actions = []

        if handler.get("primary"):
            skill_name, rationale = handler["primary"]
            recommended_actions.append(RecoveryAction(
                action_id=f"recovery_{skill_name}_primary",
                skill_name=skill_name,
                rationale=rationale,
                priority=1,
            ))

        if handler.get("fallback"):
            skill_name, rationale = handler["fallback"]
            recommended_actions.append(RecoveryAction(
                action_id=f"recovery_{skill_name}_fallback",
                skill_name=skill_name,
                rationale=rationale,
                priority=2,
            ))

        if not recommended_actions:
            recommended_actions = self._generate_generic_recovery(root_cause)

        logger.info(f"[RecoveryPlanner] Created plan for root cause: {root_cause}")

        return RecoveryPlan(
            root_cause=root_cause,
            confidence=0.8,
            recommended_actions=recommended_actions,
            estimated_success_rate=0.7,
        )

    def _classify_failure(
        self,
        failed_action: str,
        error: Optional[str],
        state: "RuntimeState",
    ) -> str:
        """Classify the root cause of failure."""
        if state.last_validation:
            issues = state.last_validation.get("root_causes", [])
            if issues:
                return issues[0]

        if error:
            error_lower = error.lower()
            if "table" in error_lower:
                return "table_structure_invalid"
            if "empty" in error_lower or "page" in error_lower:
                return "empty_page"
            if "text" in error_lower or "content" in error_lower:
                return "low_text_content"
            if "discard" in error_lower:
                return "high_discarded_ratio"
            if "backend" in error_lower:
                return "missing_backend"
            if "ocr" in error_lower:
                return "ocr_quality_low"
            if "image" in error_lower:
                return "image_extraction_failed"

        return "unknown_failure"

    def _generate_generic_recovery(self, root_cause: str) -> list["RecoveryAction"]:
        """Generate generic recovery actions when no specific handler exists."""
        from data_agent.state.runtime_state import RecoveryAction

        return [
            RecoveryAction(
                action_id="recovery_reparse",
                skill_name="reparse_skill",
                params={"backend": "hybrid-auto-engine"},
                rationale=f"Generic recovery for {root_cause}",
                priority=1,
            ),
            RecoveryAction(
                action_id="recovery_validate_again",
                skill_name="validate_skill",
                params={},
                rationale="Re-validate after recovery",
                priority=2,
            ),
        ]

    def register_handler(self, root_cause: str, primary: tuple, fallback: Optional[tuple] = None) -> None:
        """Register a new root cause handler at runtime."""
        self.ROOT_CAUSE_HANDLERS[root_cause] = {
            "primary": primary,
            "fallback": fallback,
        }