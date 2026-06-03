# Copyright (c) Data Agent Team. All rights reserved.
"""Enhanced task orchestrator with dynamic execution and condition support."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from loguru import logger

from data_agent.agent.base import AgentContext
from data_agent.agent.planner import (
    ExecutionPlan,
    TaskCondition,
    ConditionOperator,
    PlannedStep,
)
from data_agent.agent.resource_monitor import ResourceMonitor, CircuitBreaker
from data_agent.utils.task import SubTask
from data_agent.utils.exceptions import CircularDependencyError


@dataclass
class StepResult:
    """Result of a single step execution."""

    step_id: str
    success: bool
    output: Any = None
    error: str = None
    start_time: datetime = field(default_factory=datetime.utcnow)
    end_time: datetime = None
    execution_time_ms: float = 0


class DynamicTaskOrchestrator:
    """Task orchestrator with dynamic execution, conditions, and resource awareness."""

    def __init__(
        self,
        sub_agents: dict,
        resource_monitor: Optional[ResourceMonitor] = None,
        error_recovery: Optional["ErrorRecovery"] = None,
        base_max_concurrency: int = 4,
    ):
        self.sub_agents = sub_agents
        self.resource_monitor = resource_monitor or ResourceMonitor()
        self.error_recovery = error_recovery
        self.base_max_concurrency = base_max_concurrency

        # Circuit breakers per agent type
        self._circuit_breakers = {}

        # Step results for condition evaluation
        self._step_results: dict[str, StepResult] = {}

    async def execute_plan(
        self,
        plan: ExecutionPlan,
        context: AgentContext,
    ) -> dict[str, Any]:
        """Execute an execution plan with conditions."""
        self._step_results = {}
        results = {}
        completed = set()
        pending_steps = {step.step_id: step for step in plan.subtasks}

        while pending_steps:
            # Find ready steps (all dependencies satisfied and conditions met)
            ready = self._get_ready_steps(
                pending_steps, completed, plan.conditions
            )

            if not ready:
                # Check for circular dependency
                remaining = set(pending_steps.keys()) - completed
                raise CircularDependencyError(remaining)

            # Adjust concurrency based on resources
            snapshot = await self.resource_monitor.get_snapshot()
            optimal_concurrency = self.resource_monitor.calculate_optimal_concurrency(snapshot)
            batch = ready[:optimal_concurrency]

            logger.info(
                f"Executing batch of {len(batch)} steps, "
                f"optimal concurrency: {optimal_concurrency}"
            )

            # Execute batch
            batch_results = await asyncio.gather(
                *[self._execute_step(step, context) for step in batch],
                return_exceptions=True,
            )

            # Process results
            for step, result in zip(batch, batch_results):
                step_result = result if isinstance(result, StepResult) else StepResult(
                    step_id=step.step_id,
                    success=False,
                    error=str(result),
                )

                self._step_results[step.step_id] = step_result
                results[step.step_id] = step_result

                if step_result.success:
                    completed.add(step.step_id)
                    del pending_steps[step.step_id]

                    # Record success for circuit breaker
                    await self._record_success(step.agent_name)
                else:
                    logger.error(f"Step {step.step_id} failed: {step_result.error}")

                    # Handle failure
                    failed_result = await self._handle_step_failure(
                        step, step_result, context
                    )
                    if failed_result:
                        completed.add(step.step_id)
                        del pending_steps[step.step_id]
                        results[step.step_id] = failed_result

        return results

    def _get_ready_steps(
        self,
        pending_steps: dict,
        completed: set,
        conditions: dict,
    ) -> list:
        """Get steps that are ready to execute."""
        ready = []

        for step_id, step in pending_steps.items():
            # Check dependencies
            deps = getattr(step, 'dependencies', [])
            if not all(dep in completed for dep in deps):
                continue

            # Check conditions
            step_conditions = conditions.get(step_id, [])
            if step_conditions:
                if not self._evaluate_conditions(step_conditions):
                    continue

            ready.append(step)

        return ready

    def _evaluate_conditions(self, conditions: list[TaskCondition]) -> bool:
        """Evaluate whether all conditions are satisfied."""
        for condition in conditions:
            source_result = self._step_results.get(condition.source_task)
            if not source_result:
                return False

            # Get the field value from output
            output = source_result.output or {}
            actual_value = self._get_nested_field(output, condition.field_path)

            # Evaluate condition
            satisfied = self._check_condition(
                actual_value, condition.operator, condition.expected_value
            )

            if not satisfied:
                return False

        return True

    def _get_nested_field(self, obj: Any, field_path: str) -> Any:
        """Get nested field from object using dot notation."""
        if not field_path:
            return obj

        parts = field_path.split(".")
        current = obj

        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            elif hasattr(current, part):
                current = getattr(current, part)
            else:
                return None

        return current

    def _check_condition(
        self,
        actual_value: Any,
        operator: ConditionOperator,
        expected_value: Any,
    ) -> bool:
        """Check if condition is satisfied."""
        if operator == ConditionOperator.EXISTS:
            return actual_value is not None
        elif operator == ConditionOperator.NOT_EXISTS:
            return actual_value is None
        elif operator == ConditionOperator.EQUALS:
            return actual_value == expected_value
        elif operator == ConditionOperator.NOT_EQUALS:
            return actual_value != expected_value
        elif operator == ConditionOperator.GREATER_THAN:
            return actual_value > expected_value
        elif operator == ConditionOperator.LESS_THAN:
            return actual_value < expected_value
        elif operator == ConditionOperator.CONTAINS:
            return expected_value in str(actual_value)

        return True

    async def _execute_step(
        self,
        step: Any,
        context: AgentContext,
    ) -> StepResult:
        """Execute a single step."""
        start_time = datetime.utcnow()

        try:
            # Check circuit breaker
            if not await self._can_execute(step.agent_name):
                return StepResult(
                    step_id=getattr(step, 'step_id', 'unknown'),
                    success=False,
                    error=f"Circuit breaker open for {step.agent_name}",
                    start_time=start_time,
                )

            await self.resource_monitor.task_started()

            agent = self.sub_agents.get(step.agent_name)
            if not agent:
                return StepResult(
                    step_id=getattr(step, 'step_id', 'unknown'),
                    success=False,
                    error=f"Unknown agent: {step.agent_name}",
                    start_time=start_time,
                )

            # Create step context
            step_context = AgentContext(
                task_id=context.task_id,
                original_input=step.input_mapping,
                current_state=context.current_state.copy(),
                metadata={"step_id": getattr(step, 'step_id', 'unknown')},
            )

            response = await agent.execute(step_context)

            end_time = datetime.utcnow()
            execution_time = (end_time - start_time).total_seconds() * 1000

            return StepResult(
                step_id=getattr(step, 'step_id', 'unknown'),
                success=response.success,
                output=response.output,
                error=response.error,
                start_time=start_time,
                end_time=end_time,
                execution_time_ms=execution_time,
            )

        except Exception as e:
            return StepResult(
                step_id=getattr(step, 'step_id', 'unknown'),
                success=False,
                error=str(e),
                start_time=start_time,
                end_time=datetime.utcnow(),
            )

        finally:
            await self.resource_monitor.task_completed()

    async def _handle_step_failure(
        self,
        step: Any,
        result: StepResult,
        context: AgentContext,
    ) -> Optional[StepResult]:
        """Handle step failure with recovery strategies."""
        step_id = getattr(step, 'step_id', 'unknown')

        # Record failure for circuit breaker
        await self._record_failure(step.agent_name)

        # Try error recovery
        if self.error_recovery:
            try:
                recovered = await self.error_recovery.recover(
                    self.sub_agents.get(step.agent_name),
                    step,
                    context,
                    result.error,
                )
                if recovered is not None:
                    return StepResult(
                        step_id=step_id,
                        success=True,
                        output=recovered,
                    )
            except Exception as e:
                import traceback
                logger.error(f"Recovery failed: {traceback.format_exc()}")

        # Return original failure
        return result

    async def _can_execute(self, agent_name: str) -> bool:
        """Check if agent can execute (circuit breaker check)."""
        if agent_name not in self._circuit_breakers:
            self._circuit_breakers[agent_name] = CircuitBreaker()

        return await self._circuit_breakers[agent_name].can_execute()

    async def _record_success(self, agent_name: str) -> None:
        """Record success for circuit breaker."""
        if agent_name in self._circuit_breakers:
            await self._circuit_breakers[agent_name].record_success()

    async def _record_failure(self, agent_name: str) -> None:
        """Record failure for circuit breaker."""
        if agent_name in self._circuit_breakers:
            await self._circuit_breakers[agent_name].record_failure()
