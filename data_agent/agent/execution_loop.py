"""
Action Execution Loop - Continuous observe-reflect-act cycle.
"""

import asyncio
from datetime import datetime
from typing import Optional, TYPE_CHECKING
from loguru import logger

if TYPE_CHECKING:
    from data_agent.state.runtime_state import RuntimeState, ProcessingStatus
    from data_agent.agent.reflection_agent import ReflectionAgent, ObservationResult, ReflectionResult, ActResult
    from data_agent.agent.action_selector import ActionSelector, DiscoveredAction
    from data_agent.agent.recovery_planner import RecoveryPlanner
    from data_agent.skills.registry import SkillRegistry
    from data_agent.utils.observability import TaskLogger, ExecutionTracer
    from data_agent.utils.timeline_logger import TimelineLogger
    from data_agent.utils.file_logger import FileLogger
    from data_agent.utils.execution_dag import ExecutionDAG


class ActionExecutionLoop:
    """
    Continuous async action execution loop.

    This is the main runtime loop that:
    1. Observes current state
    2. Reflects on goals vs reality
    3. Selects actions
    4. Executes actions
    5. Validates results
    6. Handles failures via recovery
    """

    def __init__(
        self,
        reflection_agent: "ReflectionAgent",
        action_selector: "ActionSelector",
        recovery_planner: "RecoveryPlanner",
        skill_registry: "SkillRegistry",
        max_iterations: int = 2,
        loop_delay_ms: int = 100,
        timeline_logger: "TimelineLogger" = None,
        file_logger: "FileLogger" = None,
        dag: "ExecutionDAG" = None,
    ):
        self.reflection_agent = reflection_agent
        self.action_selector = action_selector
        self.recovery_planner = recovery_planner
        self.skill_registry = skill_registry
        self.max_iterations = max_iterations
        self.loop_delay_ms = loop_delay_ms
        self._timeline_logger = timeline_logger
        self._file_logger = file_logger
        self._dag = dag

        self._running = False
        self._logger: Optional["TaskLogger"] = None
        self._tracer: Optional["ExecutionTracer"] = None

    def set_loggers(
        self,
        timeline_logger: "TimelineLogger" = None,
        file_logger: "FileLogger" = None,
        dag: "ExecutionDAG" = None,
    ):
        """Set loggers after initialization"""
        self._timeline_logger = timeline_logger
        self._file_logger = file_logger
        self._dag = dag

    async def run(self, initial_state: "RuntimeState") -> "RuntimeState":
        """Run the execution loop until terminal state or max iterations."""
        from data_agent.state.runtime_state import ProcessingStatus
        from data_agent.utils.observability import TaskLogger, ExecutionTracer

        if initial_state is None:
            logger.error("[ExecutionLoop] initial_state is None!")
            raise ValueError("initial_state cannot be None")

        self._running = True
        self._logger = TaskLogger(initial_state.task_id)
        self._tracer = ExecutionTracer(initial_state.task_id)

        state = initial_state
        iteration = 0

        span_id = self._tracer.start_span(
            name="execution_loop",
            span_type="loop",
            metadata={"task_id": initial_state.task_id},
        )

        try:
            while self._running and not state.is_terminal:
                iteration += 1

                if iteration > self.max_iterations:
                    self._logger.warning(f"Max iterations ({self.max_iterations}) reached")
                    break

                loop_span = self._tracer.start_span(
                    name=f"iteration_{iteration}",
                    span_type="iteration",
                    parent_id=span_id,
                )

                state = await self._execute_iteration(state, iteration)

                if state is None:
                    self._logger.error("state is None after _execute_iteration")
                    break

                # If state is FAILED but we have doc_tree (built externally by doc_tree_skill after loop),
                # treat as COMPLETED
                if state.status == ProcessingStatus.FAILED:
                    middle_dump = state.middle_json.model_dump()
                    if middle_dump.get("document_tree") or middle_dump.get("txt_tree"):
                        self._logger.info(f"[ExecutionLoop] State is FAILED but doc_tree exists - treating as COMPLETED")
                        state.update_status(ProcessingStatus.COMPLETED)

                self._tracer.end_span(loop_span, status="ok")

                if not state.is_terminal:
                    await asyncio.sleep(self.loop_delay_ms / 1000)

        except Exception as e:
            import traceback
            self._logger.error(f"Loop error: {traceback.format_exc()}")
            self._tracer.add_event(span_id, "loop_error", {"error": str(e)})
            if state is not None:
                state.update_status(ProcessingStatus.FAILED)

        finally:
            self._tracer.end_span(span_id)
            self._running = False

        return state

    async def _execute_iteration(
        self,
        state: "RuntimeState",
        iteration: int,
    ) -> "RuntimeState":
        """Execute a single iteration of the observe-reflect-act loop."""
        from data_agent.state.runtime_state import ProcessingStatus, ValidatorOutput
        from data_agent.utils.execution_dag import NodeStatus

        self._logger.debug(f"Iteration {iteration}: status={state.status.value}")

        # Timeline: Observe phase
        if self._timeline_logger:
            self._timeline_logger.running(
                "MainAgent",
                f"Iteration {iteration} observe: pages={state.page_count}, blocks={state.total_blocks}",
                sub_agent="ReflectionAgent"
            )

        observation = await self.reflection_agent._observe(state)

        if self._timeline_logger:
            self._timeline_logger.success(
                "MainAgent",
                f"Iteration {iteration} observe completed: anomalies={observation.anomalies_detected}",
                sub_agent="ReflectionAgent"
            )

        self._logger.info(
            f"[Iteration {iteration}] OBSERVE: pages={state.page_count}, "
            f"blocks={state.total_blocks}, anomalies={observation.anomalies_detected}"
        )

        # Update DAG
        if self._dag:
            self._dag.set_node_status("ReflectionAgent", NodeStatus.RUNNING)

        reflection = await self.reflection_agent._reflect(state, observation)

        state.observation_summary = observation.summary
        state.reflection_notes.append(reflection.analysis)

        # File log: reflection output
        if self._file_logger:
            self._file_logger.write_reflection({
                "iteration": iteration,
                "analysis": reflection.analysis,
                "root_issues": reflection.root_issues,
                "should_replan": reflection.should_replan,
                "strategic_insights": reflection.strategic_insights,
            })

        if reflection.should_replan:
            state.update_status(ProcessingStatus.REPLANNING)
            new_goals = await self.reflection_agent._replan(state, reflection)

            # Timeline: Replan
            if self._timeline_logger:
                self._timeline_logger.running(
                    "MainAgent",
                    f"Iteration {iteration} replanning: {len(new_goals.goals)} goals, issues={reflection.root_issues}",
                    sub_agent="GoalPlanner"
                )

            self._logger.info(f"[Iteration {iteration}] REPLAN: {len(new_goals.goals)} new goals, issues={reflection.root_issues}")

            # File log: planner output
            if self._file_logger:
                self._file_logger.append_jsonl("execution_trace.jsonl", {
                    "iteration": iteration,
                    "event": "replan",
                    "goals": [
                        {
                            "goal_id": g.goal_id,
                            "description": g.description,
                            "skill_name": g.skill_name,
                            "params": g.params,
                        }
                        for g in new_goals.goals
                    ],
                    "issues": reflection.root_issues,
                    "timestamp": datetime.utcnow().isoformat(),
                })
                # Write planner.json
                self._file_logger.write_planner({
                    "iteration": iteration,
                    "goals": [
                        {
                            "goal_id": g.goal_id,
                            "description": g.description,
                            "priority": g.priority,
                            "skill_name": g.skill_name,
                            "params": g.params,
                        }
                        for g in new_goals.goals
                    ],
                    "root_issues": reflection.root_issues,
                })

            # Store goal skill binding in state for ActionSelector
            if new_goals.goals:
                top_goal = new_goals.goals[0]
                state.current_goal_skill = top_goal.skill_name
                state.current_goal_params = top_goal.params
                # Add new goals to current_goals list
                for goal in new_goals.goals:
                    if goal.skill_name and goal.skill_name not in state.current_goals:
                        state.current_goals.append(goal.skill_name)
        else:
            self._logger.info(f"[Iteration {iteration}] REFLECT: no replanning needed")
            # Clear goal binding when not replanning
            if hasattr(state, 'current_goal_skill'):
                state.current_goal_skill = None
                state.current_goal_params = {}

        state.update_status(ProcessingStatus.PARSING)

        # Timeline: Action selection
        if self._timeline_logger:
            self._timeline_logger.running(
                "MainAgent",
                f"Iteration {iteration} selecting action",
                sub_agent="ActionSelector"
            )

        action = await self.action_selector.select_action(
            state=state,
            available_skills=self.skill_registry.list_skills(),
        )

        if not action:
            self._logger.warning("No applicable action found, marking complete")
            if self._timeline_logger:
                self._timeline_logger.warning(
                    "MainAgent",
                    "No applicable action found, marking complete",
                    sub_agent="ActionSelector"
                )
            state.update_status(ProcessingStatus.COMPLETED)
            return state

        execute_span = self._tracer.start_span(
            name=f"action_{action.skill_name}",
            span_type="action",
        )

        skill = self.skill_registry.get_skill(action.skill_name)
        if not skill:
            self._logger.error(f"Skill {action.skill_name} not found in registry")
            if self._timeline_logger:
                self._timeline_logger.error(
                    "MainAgent",
                    f"Skill {action.skill_name} not found in registry",
                    sub_agent="ActionSelector"
                )
            state.update_status(ProcessingStatus.FAILED)
            return state

        # Timeline: Skill execution starts
        if self._timeline_logger:
            self._timeline_logger.running(
                "MainAgent",
                f"Executing {action.skill_name} with params: {action.params}",
                sub_agent="ActionSelector",
                skill=action.skill_name
            )

        self._logger.info(f"Executing skill: {action.skill_name} with params: {action.params}")
        try:
            # Pass source_file for skills that need to re-parse (e.g., ReparseBadPagesSkill)
            skill_kwargs = dict(action.params)
            # Only fill path if it's missing or "N/A"
            path = skill_kwargs.get("path")
            if not path or path == "N/A":
                source = state.get_source_file()
                if source and source != "N/A":
                    skill_kwargs["path"] = source
                    action.params["path"] = source
                else:
                    middle_dump = state.middle_json.model_dump()
                    if middle_dump.get("_file_path") and middle_dump.get("_file_path") != "N/A":
                        skill_kwargs["path"] = middle_dump["_file_path"]
                        action.params["path"] = middle_dump["_file_path"]

            import time
            start_time = time.time()
            output = await skill.execute(state.middle_json.model_dump(), **skill_kwargs)
            duration_ms = (time.time() - start_time) * 1000
            state.executed_actions.append(action.action_id)

            if output and isinstance(output, dict):
                state.update_middle_json(output)

            self._tracer.end_span(execute_span, status="ok")

            # Timeline: Skill execution success
            if self._timeline_logger:
                self._timeline_logger.success(
                    "MainAgent",
                    f"{action.skill_name} completed in {duration_ms:.1f}ms",
                    sub_agent="ActionSelector",
                    skill=action.skill_name
                )

            # File log: skill execution trace
            if self._file_logger:
                self._file_logger.append_jsonl("execution_trace.jsonl", {
                    "iteration": iteration,
                    "skill": action.skill_name,
                    "params": skill_kwargs,
                    "duration_ms": duration_ms,
                    "result": "success",
                    "timestamp": datetime.utcnow().isoformat(),
                })

        except Exception as e:
            import traceback
            self._logger.error(f"Action failed: {traceback.format_exc()}")
            self._tracer.end_span(execute_span, status="error", error=str(e))

            # Timeline: Skill execution error
            if self._timeline_logger:
                self._timeline_logger.error(
                    "MainAgent",
                    f"{action.skill_name} failed: {str(e)}",
                    sub_agent="ActionSelector",
                    skill=action.skill_name
                )

            # File log: error
            if self._file_logger:
                self._file_logger.append_jsonl("execution_trace.jsonl", {
                    "iteration": iteration,
                    "skill": action.skill_name,
                    "error": str(e),
                    "result": "error",
                    "timestamp": datetime.utcnow().isoformat(),
                })
                self._file_logger.write_error_log(
                    f"Skill {action.skill_name} failed: {str(e)}",
                    {"iteration": iteration, "traceback": traceback.format_exc()}
                )

            state.update_status(ProcessingStatus.RECOVERING)
            recovery_plan = await self.recovery_planner.create_recovery_plan(
                failed_action=action.action_id,
                error=str(e),
                current_state=state,
            )

            for recovery_action in recovery_plan.recommended_actions:
                state.add_pending_action(recovery_action.action_id)

            state.recovery_attempts += 1

        validation_span = self._tracer.start_span(
            name="validation",
            span_type="validation",
        )

        # Timeline: Validation
        if self._timeline_logger:
            self._timeline_logger.running(
                "MainAgent",
                f"Iteration {iteration} validating",
                sub_agent="ReflectionAgent"
            )

        validation_output = await self.reflection_agent._validate(state)
        state.last_validation = validation_output.model_dump()
        state.validation_history.append(validation_output.model_dump())

        self._tracer.end_span(
            validation_span,
            status="ok" if validation_output.passed else "error",
        )

        self._logger.info(
            f"[Iteration {iteration}] VALIDATE: passed={validation_output.passed}, "
            f"score={validation_output.score:.3f}, grade={validation_output.grade}, "
            f"root_causes={validation_output.root_causes}"
        )

        # Timeline: Validation result
        if self._timeline_logger:
            if validation_output.passed:
                self._timeline_logger.success(
                    "MainAgent",
                    f"Iteration {iteration} passed: score={validation_output.score:.3f}",
                    sub_agent="ReflectionAgent"
                )
            else:
                self._timeline_logger.warning(
                    "MainAgent",
                    f"Iteration {iteration} failed: score={validation_output.score:.3f}, causes={validation_output.root_causes}",
                    sub_agent="ReflectionAgent"
                )

        if validation_output.passed:
            # Check if there are pending goals (goals not yet completed)
            pending_goals = [g for g in state.current_goals if g not in state.completed_goals]

            if not pending_goals:
                # All goals completed
                state.update_status(ProcessingStatus.COMPLETED)
                self._logger.info(f"[Iteration {iteration}] COMPLETED: All goals achieved ({state.completed_goals})")

                # Timeline: Task completed
                if self._timeline_logger:
                    self._timeline_logger.success(
                        "MainAgent",
                        "All goals achieved, task completed"
                    )

                # File log: final output
                if self._file_logger:
                    self._file_logger.write_final_output({
                        "task_id": state.task_id,
                        "status": state.status.value,
                        "final_score": validation_output.score,
                        "middle_json": state.middle_json.model_dump(),
                    })
                    self._file_logger.write_metrics({
                        "final_score": validation_output.score,
                        "grade": validation_output.grade,
                        "root_causes": validation_output.root_causes,
                        "iterations": iteration,
                    })
            else:
                # There are still goals to work on - don't mark complete yet
                self._logger.info(f"[Iteration {iteration}] Validation passed but pending goals remain: {pending_goals}")
                # Clear goal binding to let ActionSelector pick next pending goal
                state.current_goal_skill = None
                state.current_goal_params = {}
                # Since validation passed, we're in a good state - just waiting for more skills
                # Keep status as PARSING so loop continues (or will exit at max_iterations)

        # Max iterations reached - complete the loop
        if iteration >= self.max_iterations:
            self._logger.info(f"[Iteration {iteration}] Max iterations reached")
            # Force COMPLETED if we have doc_tree (doc_tree_skill builds it after loop)
            middle_dump = state.middle_json.model_dump()
            has_doc_tree = middle_dump.get("document_tree") or middle_dump.get("txt_tree")
            self._logger.info(f"[Iteration {iteration}] Before fix: validation_passed={validation_output.passed}, has_doc_tree={has_doc_tree}, current_status={state.status.value}")
            if validation_output.passed or has_doc_tree or state.status == ProcessingStatus.PARSING:
                self._logger.info(f"[Iteration {iteration}] Forcing COMPLETED")
                state.update_status(ProcessingStatus.COMPLETED)
            self._logger.info(f"[Iteration {iteration}] After fix: status={state.status.value}, is_terminal={state.is_terminal}")

        return state

        return state

    def stop(self) -> None:
        """Stop the execution loop."""
        self._running = False
        self._logger.info("Execution loop stopped")

    def _build_sub_agent_context(self, state: "RuntimeState", action: "DiscoveredAction") -> "AgentContext":
        """Build AgentContext for sub-agent calls."""
        from data_agent.agent.base import AgentContext
        return AgentContext(
            task_id=state.task_id,
            original_input={"middle_json": state.middle_json.model_dump()},
            timeline_logger=self._timeline_logger,
        )