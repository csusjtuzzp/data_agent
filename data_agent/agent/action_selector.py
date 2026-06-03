"""
ActionSelector - Dynamic action discovery via registry.
"""

from pydantic import BaseModel, Field
from typing import Optional, TYPE_CHECKING
from datetime import datetime
from loguru import logger

if TYPE_CHECKING:
    from data_agent.state.runtime_state import RuntimeState
    from data_agent.skills.registry import SkillRegistry


class DiscoveredAction(BaseModel):
    """An action discovered from the registry."""
    action_id: str
    skill_name: str
    description: str
    applicability_score: float = 0.0
    params: dict = Field(default_factory=dict)
    preconditions: list[str] = Field(default_factory=list)
    postconditions: list[str] = Field(default_factory=list)


class ActionSelector:
    """
    Dynamic action discovery via registry.

    Selects actions based on:
    1. Current runtime state
    2. Registry-available skills
    3. Preconditions/postconditions matching
    4. Historical success rates
    """

    def __init__(
        self,
        skill_registry: "SkillRegistry",
        action_history: Optional[dict] = None,
    ):
        self.skill_registry = skill_registry
        self.action_history = action_history or {}

    async def select_action(
        self,
        state: "RuntimeState",
        available_skills: list[str],
    ) -> Optional[DiscoveredAction]:
        """Select the best action for current state.

        If state has pending goals, only consider skills matching those goals.
        If state has current_goal_skill bound from replanning, use that.
        """
        candidates = []

        # Check if there's a goal-bound skill from replanning
        goal_skill_name = getattr(state, 'current_goal_skill', None)
        goal_params = getattr(state, 'current_goal_params', {}) or {}

        # Get pending goals (goals not yet completed)
        pending_goals = [g for g in state.current_goals if g not in state.completed_goals and g not in state.failed_goals]

        for skill_name in available_skills:
            skill = self.skill_registry.get_skill(skill_name)
            if not skill:
                continue

            metadata = getattr(skill, "action_metadata", {})

            # If goal has a specific skill binding, skip precondition check and other skills
            if goal_skill_name:
                if skill_name != goal_skill_name:
                    continue
                # Goal-bound skill: skip precondition check, force selection
                score = 1.0
                params = dict(goal_params)
                if not params.get("path") or params.get("path") == "N/A":
                    source = state.get_source_file()
                    if source and source != "N/A":
                        params["path"] = source

                candidates.append(DiscoveredAction(
                    action_id=f"action_{skill_name}_{datetime.utcnow().timestamp()}",
                    skill_name=skill_name,
                    description=metadata.get("description", ""),
                    applicability_score=score,
                    params=params,
                    preconditions=metadata.get("preconditions", []),
                    postconditions=metadata.get("postconditions", []),
                ))
                break

            # If there are pending goals, only consider skills that are pending
            if pending_goals and skill_name not in pending_goals:
                continue

            preconditions_met = all(
                self._check_precondition(pc, state)
                for pc in metadata.get("preconditions", [])
            )

            if not preconditions_met:
                continue

            score = self._calculate_applicability(
                skill_name=skill_name,
                state=state,
                metadata=metadata,
            )

            if score > 0:
                # Build params with state-based fallback for file path
                params = dict(metadata.get("default_params", {}))
                if not params.get("path") and not params.get("file_path"):
                    source = state.get_source_file()
                    if source:
                        params["path"] = source

                candidates.append(DiscoveredAction(
                    action_id=f"action_{skill_name}_{datetime.utcnow().timestamp()}",
                    skill_name=skill_name,
                    description=metadata.get("description", ""),
                    applicability_score=score,
                    params=params,
                    preconditions=metadata.get("preconditions", []),
                    postconditions=metadata.get("postconditions", []),
                ))

        if not candidates:
            logger.warning(
                f"[ActionSelector] No applicable actions found. "
                f"Available skills: {available_skills}, "
                f"page_count: {state.page_count}, "
                f"last_validation: {state.last_validation}"
            )
            return None

        candidates.sort(key=lambda x: x.applicability_score, reverse=True)
        best = candidates[0]

        logger.info(
            f"[ActionSelector] Selected {best.skill_name} "
            f"(score={best.applicability_score:.3f})"
        )

        return best

    def _check_precondition(self, precondition: str, state: "RuntimeState") -> bool:
        """Check if a precondition is satisfied."""
        precondition_map = {
            "has_content": state.page_count > 0,
            "has_tables": any(
                p.preproc_blocks
                for p in state.middle_json.pdf_info
                if any(b.type == "table" for b in p.preproc_blocks)
            ),
            "has_images": any(
                p.preproc_blocks
                for p in state.middle_json.pdf_info
                if any(b.type == "image" for b in p.preproc_blocks)
            ),
            "validation_failed": (
                state.last_validation is not None
                and not state.last_validation.get("passed", True)
            ),
            "has_empty_pages": any(not p.has_content for p in state.middle_json.pdf_info),
            "low_quality": (
                state.last_validation is not None
                and state.last_validation.get("score", 1.0) < 0.7
            ),
            "high_discarded_ratio": state.middle_json.discarded_ratio > 0.2,
            "can_retry": state.should_retry(),
            "has_bad_pages": self._check_bad_pages(state),
            "needs_doc_tree": (
                "document_tree" not in state.middle_json
                and "txt_tree" not in state.middle_json
            ),
        }
        return precondition_map.get(precondition, True)

    def _check_bad_pages(self, state: "RuntimeState") -> bool:
        """Check if state has bad pages that need re-parsing."""
        # Check from validation result
        if state.last_validation:
            summary = state.last_validation.get("summary", {})
            bad_pages = summary.get("bad_pages", [])
            if bad_pages:
                return True
            # Also check for low_text_pages and empty_pages
            if summary.get("low_text_pages") or summary.get("empty_pages"):
                return True
        # Check from middle_json markers
        middle_json_dict = state.middle_json.model_dump()
        if middle_json_dict.get("_bad_page_indices"):
            return True
        return False

    def _calculate_applicability(
        self,
        skill_name: str,
        state: "RuntimeState",
        metadata: dict,
    ) -> float:
        """Calculate how applicable this skill is to current state."""
        score = 0.5

        last_action = state.executed_actions[-1] if state.executed_actions else None
        if last_action:
            history_entry = self.action_history.get(last_action, {})
            if history_entry.get("success"):
                score += 0.1

        if state.last_validation:
            val_output = state.last_validation
            if not val_output.get("passed", True):
                issues = val_output.get("root_causes", [])

                skill_issues = metadata.get("addresses_issues", [])
                matching = set(issues) & set(skill_issues)
                score += len(matching) * 0.15

        if skill_name in state.failed_goals:
            score -= 0.3

        return max(0.0, min(1.0, score))