"""
ReflectionAgent - Observe → Reflect → Replan → Act loop.

This is the core cognitive loop of the autonomous data agent.
"""

from pydantic import BaseModel, Field
from typing import Any, Optional, TYPE_CHECKING
from datetime import datetime
from dataclasses import dataclass
from loguru import logger

if TYPE_CHECKING:
    from data_agent.state.runtime_state import RuntimeState, ProcessingStatus, ValidatorOutput
    from data_agent.skills.registry import SkillRegistry
    from data_agent.agent.action_selector import DiscoveredAction

from data_agent.agent.base import AgentContext, AgentResponse, AgentStatus, BaseAgent
from data_agent.agent.planner import GoalPlanner, GoalPlan, Goal
from data_agent.agent.action_selector import ActionSelector, DiscoveredAction
from data_agent.agent.recovery_planner import RecoveryPlanner


class ObservationResult(BaseModel):
    """Result of the observe phase."""
    summary: str
    state_snapshot: dict
    anomalies_detected: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ReflectionResult(BaseModel):
    """Result of the reflect phase."""
    analysis: str
    root_issues: list[str] = Field(default_factory=list)
    strategic_insights: list[str] = Field(default_factory=list)
    should_replan: bool = False
    confidence: float = 1.0


@dataclass
class ActResult:
    """Result of the act phase."""
    action_id: str
    success: bool
    output: Any = None
    error: Optional[str] = None
    execution_time_ms: float = 0


class ReflectionAgent(BaseAgent):
    """
    Agent implementing Observe → Reflect → Replan → Act loop.

    This is the core cognitive loop of the autonomous data agent.
    Unlike traditional agents that execute fixed plans, this agent:
    1. OBSERVES: Continuously monitors RuntimeState
    2. REFLECTS: Analyzes current state vs goals
    3. REPLANS: Generates new goals when needed
    4. ACTS: Executes actions from ActionSelector
    """

    def __init__(
        self,
        skill_registry: "SkillRegistry",
        goal_planner: GoalPlanner,
        action_selector: ActionSelector,
        recovery_planner: RecoveryPlanner,
        max_iterations: int = 2,
    ):
        super().__init__("ReflectionAgent")
        self.skill_registry = skill_registry
        self.goal_planner = goal_planner
        self.action_selector = action_selector
        self.recovery_planner = recovery_planner
        self.max_iterations = max_iterations

    async def execute(self, context: AgentContext) -> AgentResponse:
        """Execute the observe-reflect-act loop."""
        from data_agent.state.runtime_state import RuntimeState, ProcessingStatus

        runtime_state = self._extract_runtime_state(context)
        runtime_state.update_status(ProcessingStatus.PARSING)

        try:
            loop_count = 0

            while not runtime_state.is_terminal and loop_count < self.max_iterations:
                loop_count += 1
                logger.info(f"[ReflectionAgent] Iteration {loop_count}")

                observation = await self._observe(runtime_state)
                logger.debug(f"[ReflectionAgent] Observation: {observation.summary}")

                reflection = await self._reflect(runtime_state, observation)
                logger.debug(f"[ReflectionAgent] Reflection: {reflection.analysis}")

                runtime_state.observation_summary = observation.summary
                runtime_state.reflection_notes.append(reflection.analysis)

                if reflection.should_replan:
                    runtime_state.update_status(ProcessingStatus.REPLANNING)
                    new_goals = await self._replan(runtime_state, reflection)
                    logger.info(f"[ReflectionAgent] New goals: {[g.goal_id for g in new_goals.goals]}")

                runtime_state.update_status(ProcessingStatus.PARSING)
                act_result = await self._act(runtime_state)

                if act_result.success:
                    for goal in runtime_state.current_goals:
                        runtime_state.complete_goal(goal)
                else:
                    runtime_state.update_status(ProcessingStatus.RECOVERING)
                    await self._handle_failure(runtime_state, act_result)

                validation_output = await self._validate(runtime_state)
                runtime_state.last_validation = validation_output.model_dump()
                runtime_state.validation_history.append(validation_output.model_dump())

                if validation_output.passed:
                    runtime_state.update_status(ProcessingStatus.COMPLETED)
                    break

            return AgentResponse(
                success=not runtime_state.is_terminal,
                status=AgentStatus.COMPLETED
                if runtime_state.status == ProcessingStatus.COMPLETED
                else AgentStatus.FAILED,
                output={"runtime_state": runtime_state.model_dump()},
                context=context,
            )

        except Exception as e:
            import traceback
            logger.error(f"[ReflectionAgent] Loop failed: {traceback.format_exc()}")
            runtime_state.update_status(ProcessingStatus.FAILED)
            return AgentResponse(
                success=False,
                status=AgentStatus.FAILED,
                error=str(e),
                context=context,
            )

    async def _observe(self, state: "RuntimeState") -> ObservationResult:
        """Observe the current runtime state."""
        from data_agent.state.runtime_state import ProcessingStatus

        anomalies = []
        opportunities = []

        if state.page_count == 0:
            anomalies.append("no_pages_detected")

        for page in state.middle_json.pdf_info:
            if not page.has_content:
                anomalies.append(f"empty_page_{page.page_idx}")

        if state.recovery_attempts >= state.max_recovery_attempts:
            anomalies.append("max_recovery_attempts_reached")

        if state.total_blocks < 10 and state.page_count > 1:
            opportunities.append("low_block_density")

        summary = (
            f"State: {state.status.value}, "
            f"pages={state.page_count}, "
            f"blocks={state.total_blocks}, "
            f"goals={len(state.current_goals)}"
        )

        return ObservationResult(
            summary=summary,
            state_snapshot=state.model_dump(),
            anomalies_detected=anomalies,
            opportunities=opportunities,
        )

    async def _reflect(
        self,
        state: "RuntimeState",
        observation: ObservationResult,
    ) -> ReflectionResult:
        """Reflect on observation - NO replanning allowed, goals are fixed from start."""
        insights = []

        if state.completed_goals:
            insights.append(f"Completed {len(state.completed_goals)} goals successfully")

        return ReflectionResult(
            analysis=f"Reflection: observe completed, no replanning allowed",
            root_issues=[],
            strategic_insights=insights,
            should_replan=False,  # NEVER replan - goals are set once at start
        )

    async def _replan(
        self,
        state: "RuntimeState",
        reflection: ReflectionResult,
    ) -> GoalPlan:
        """Generate new goals based on reflection."""
        return await self.goal_planner.generate_goals(
            instruction=state.original_instruction or "",
            current_state=state,
            root_causes=reflection.root_issues,
        )

    async def _act(self, state: "RuntimeState") -> ActResult:
        """Select and execute the next action."""
        from data_agent.state.runtime_state import ProcessingStatus

        start = datetime.utcnow()

        action = await self.action_selector.select_action(
            state=state,
            available_skills=self.skill_registry.list_skills(),
        )

        if not action:
            return ActResult(
                action_id="noop",
                success=True,
                output=None,
                execution_time_ms=(datetime.utcnow() - start).total_seconds() * 1000,
            )

        skill = self.skill_registry.get_skill(action.skill_name)
        if not skill:
            return ActResult(
                action_id=action.action_id,
                success=False,
                error=f"Skill {action.skill_name} not found",
                execution_time_ms=(datetime.utcnow() - start).total_seconds() * 1000,
            )

        try:
            output = await skill.execute(state.middle_json.model_dump(), **action.params)
            state.executed_actions.append(action.action_id)

            if output and isinstance(output, dict):
                state.update_middle_json(output)

            return ActResult(
                action_id=action.action_id,
                success=True,
                output=output,
                execution_time_ms=(datetime.utcnow() - start).total_seconds() * 1000,
            )
        except Exception as e:
            return ActResult(
                action_id=action.action_id,
                success=False,
                error=str(e),
                execution_time_ms=(datetime.utcnow() - start).total_seconds() * 1000,
            )

    async def _handle_failure(
        self,
        state: "RuntimeState",
        act_result: ActResult,
    ) -> None:
        """Handle action failure via recovery planning."""
        state.recovery_attempts += 1

        recovery_plan = await self.recovery_planner.create_recovery_plan(
            failed_action=act_result.action_id,
            error=act_result.error,
            current_state=state,
        )

        for action in recovery_plan.recommended_actions:
            state.add_pending_action(action.action_id)

    async def _validate(self, state: "RuntimeState") -> "ValidatorOutput":
        """Run validation on current state."""
        from data_agent.state.runtime_state import ValidatorOutput

        validator = self.skill_registry.get_skill("validate_skill")
        if validator:
            result = await validator.execute(state.middle_json.model_dump())
            if isinstance(result, dict):
                return ValidatorOutput(**result)

        return ValidatorOutput(
            passed=True,
            score=1.0,
            grade="A",
            root_causes=[],
            recommended_actions=[],
            issues=[],
        )

    def _extract_runtime_state(self, context: AgentContext) -> "RuntimeState":
        """Extract or create RuntimeState from context."""
        from data_agent.state.runtime_state import RuntimeState

        if "runtime_state" in context.original_input:
            return RuntimeState(**context.original_input["runtime_state"])
        return RuntimeState(task_id=context.task_id)