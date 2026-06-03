# Copyright (c) Data Agent Team. All rights reserved.
"""Task and goal planners for intelligent task decomposition."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pydantic import BaseModel, Field
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from data_agent.state.runtime_state import RuntimeState

from loguru import logger

from data_agent.agent.llm_client import BaseLLMClient, OpenAILLMClient
from data_agent.utils.task import SubTask


# =============================================================================
# Goal-based Planning (for autonomous ReflectionAgent)
# =============================================================================


class Goal(BaseModel):
    """A single goal to achieve."""
    goal_id: str
    description: str
    priority: int = 0
    dependencies: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    # Skill binding for execution
    skill_name: Optional[str] = None
    params: dict = Field(default_factory=dict)


class GoalPlan(BaseModel):
    """Complete goal-based plan."""
    goals: list[Goal] = Field(default_factory=list)
    current_goal_index: int = 0

    @property
    def current_goal(self) -> Optional[Goal]:
        if 0 <= self.current_goal_index < len(self.goals):
            return self.goals[self.current_goal_index]
        return None

    @property
    def is_complete(self) -> bool:
        return self.current_goal_index >= len(self.goals)

    def advance(self) -> None:
        """Move to next goal."""
        self.current_goal_index += 1


class BaseGoalPlanner(ABC):
    """Abstract goal planner."""

    @abstractmethod
    async def generate_goals(
        self,
        instruction: str,
        current_state: "RuntimeState",
        root_causes: list[str],
    ) -> GoalPlan:
        """Generate goals based on current state and issues."""
        pass

    def _get_skill_descriptions_from_registry(self, skill_registry) -> str:
        """Build skill descriptions string from registry."""
        if not skill_registry:
            return "（无可用技能信息）"

        lines = []
        for skill_name in skill_registry.list_skills():
            skill = skill_registry.get_skill(skill_name)
            metadata = skill_registry.get_metadata(skill_name)

            # 获取技能描述和参数信息
            desc = metadata.description if metadata else f"{skill_name}技能"
            addresses = metadata.addresses_issues if metadata else []
            default_params = metadata.default_params if metadata else {}
            param_descriptions = metadata.param_descriptions if metadata else {}

            # 构建技能描述
            if default_params:
                param_parts = []
                for k, v in default_params.items():
                    param_desc = param_descriptions.get(k, "")
                    if param_desc:
                        param_parts.append(f"{k}: {param_desc}")
                    else:
                        param_parts.append(f"{k}: {v}")
                param_str = ", ".join(param_parts)
            else:
                param_str = "无固定参数"
            addresses_str = ", ".join(addresses) if addresses else "通用"

            line = f"- {skill_name}: {desc}（解决: {addresses_str}，参数: {param_str}）"
            lines.append(line)

        return "\n".join(lines) if lines else "（无可用技能信息）"


class GoalPlanner(BaseGoalPlanner):
    """
    Goal-based planner for runtime replanning.

    Key difference from step-based planning:
    - Generates GOALS (what to achieve)
    - NOT steps (how to achieve it)
    - Actions are selected at runtime by ActionSelector
    """

    def __init__(self, llm_client: Optional["BaseLLMClient"] = None, skill_registry=None):
        self.llm_client = llm_client
        self.skill_registry = skill_registry
        self.timeline_logger = None

    def set_timeline_logger(self, timeline_logger) -> None:
        """Set timeline logger for logging LLM calls."""
        self.timeline_logger = timeline_logger

    def _log_to_timeline(self, level: str, action: str, status=None, extra: dict = None) -> None:
        """Log to timeline_logger if available."""
        tl = self.timeline_logger
        if not tl:
            return

        msg = action
        if extra:
            extra_str = ", ".join(f"{k}={v}" for k, v in extra.items() if v is not None)
            if extra_str:
                msg = f"{action} | {extra_str}"

        from data_agent.utils.timeline_logger import LogStatus
        if status == "success":
            tl.success("GoalPlanner", msg, sub_agent="GoalPlanner")
        elif status == "error":
            tl.error("GoalPlanner", msg, sub_agent="GoalPlanner")
        elif status == "running":
            tl.running("GoalPlanner", msg, sub_agent="GoalPlanner")
        else:
            tl.running("GoalPlanner", msg, sub_agent="GoalPlanner")

    ROOT_CAUSE_TO_GOALS = {
        "no_pages_detected": [
            Goal(
                goal_id="parse_document",
                description="Parse document via MinerU to get pages",
                priority=0,
                success_criteria=["page_count > 0"],
                skill_name="mineru_parse_skill",
                params={},
            ),
        ],
        "table_structure_invalid": [
            Goal(
                goal_id="fix_tables",
                description="Repair all table structures with low quality scores",
                priority=1,
                success_criteria=["no table_structure_invalid issues"],
                skill_name="repair_table_skill",
                params={},
            ),
        ],
        "low_text_content": [
            Goal(
                goal_id="improve_text_extraction",
                description="Improve text extraction quality by switching backend or reparse",
                priority=1,
                success_criteria=["text_content_length > threshold"],
                skill_name="switch_backend_skill",
                params={},
            ),
        ],
        "empty_page": [
            Goal(
                goal_id="handle_empty_pages",
                description="Handle or remove empty pages",
                priority=2,
                success_criteria=["empty_pages_ratio < 0.1"],
                skill_name="remove_empty_pages_skill",
                params={},
            ),
        ],
        "high_discarded_ratio": [
            Goal(
                goal_id="reduce_discards",
                description="Reduce the ratio of discarded blocks",
                priority=2,
                success_criteria=["discarded_ratio < 0.2"],
                skill_name="lower_score_skill",
                params={},
            ),
        ],
        "ocr_quality_low": [
            Goal(
                goal_id="improve_ocr",
                description="Improve OCR quality",
                priority=1,
                success_criteria=["ocr_confidence > threshold"],
                skill_name="switch_backend_skill",
                params={},
            ),
        ],
        "image_extraction_failed": [
            Goal(
                goal_id="fix_image_extraction",
                description="Fix image extraction issues",
                priority=2,
                success_criteria=["images_extracted"],
                skill_name=None,
                params={},
            ),
        ],
        "needs_doc_tree": [
            Goal(
                goal_id="build_doc_tree",
                description="Build semantic document tree with chapter/section hierarchy",
                priority=3,
                success_criteria=["document_tree_generated"],
                skill_name="doc_tree_skill",
                params={},
            ),
        ],
    }

    async def generate_goals(
        self,
        instruction: str,
        current_state: "RuntimeState",
        root_causes: list[str],
    ) -> GoalPlan:
        """Generate goals based on current state and issues.

        Uses LLM if available for intelligent goal generation,
        otherwise falls back to rule-based mapping.
        """
        if self.llm_client:
            logger.info("[GoalPlanner] Using LLM for goal generation")
            return await self._generate_goals_with_llm(instruction, current_state, root_causes)
        else:
            logger.info("[GoalPlanner] Using rule-based goal generation")
            return self._generate_goals_rule_based(instruction, root_causes, current_state)

    async def _generate_goals_with_llm(
        self,
        instruction: str,
        current_state: "RuntimeState",
        root_causes: list[str],
    ) -> GoalPlan:
        """Use LLM to generate goals based on current state and root causes."""
        state_summary = {
            "page_count": current_state.page_count,
            "total_blocks": current_state.total_blocks,
            "status": current_state.status.value,
            "completed_goals": current_state.completed_goals,
            "failed_goals": current_state.failed_goals,
            "last_validation": current_state.last_validation,
            "source_file": current_state.get_source_file() if hasattr(current_state, 'get_source_file') else getattr(current_state, 'source_file', 'N/A'),
        }

        # 从技能注册表获取技能描述信息
        skill_descriptions = self._get_skill_descriptions_from_registry(self.skill_registry)

        try:
            # Log LLM request prompt to timeline and console
            self._log_to_timeline("info", "GoalPlanner LLM request", status="running", extra={
                "instruction": instruction[:100],
                "state_summary": str(state_summary),
                "root_causes": str(root_causes),
                "skill_count": str(len(skill_descriptions))
            })
            logger.info(f"[GoalPlanner] LLM request - instruction: {instruction[:100]}...")

            # 使用 LLM 生成目标
            llm_result = await self.llm_client.plan_goals(
                instruction=instruction,
                state=state_summary,
                root_causes=root_causes,
                skill_descriptions=skill_descriptions,
            )

            print(llm_result)

            # Log LLM response to timeline and console
            self._log_to_timeline("info", f"GoalPlanner LLM response", status="success", extra={
                "goals_count": len(llm_result.get('goals', [])),
                "reasoning": str(llm_result.get('reasoning', 'N/A'))[:200]
            })
            logger.info(f"[GoalPlanner] LLM response - goals count: {len(llm_result.get('goals', []))}")

            goals = []
            for goal_data in llm_result.get("goals", []):
                goal = Goal(
                    goal_id=goal_data.get("goal_id", f"goal_{len(goals)}"),
                    description=goal_data.get("description", ""),
                    priority=goal_data.get("priority", 1),
                    dependencies=goal_data.get("dependencies", []),
                    success_criteria=goal_data.get("success_criteria", []),
                    metadata={"reasoning": llm_result.get("reasoning", "")},
                    skill_name=goal_data.get("skill_name"),
                    params=goal_data.get("params", {}),
                )
                goals.append(goal)

            if not goals:
                return self._generate_goals_rule_based(instruction, root_causes)

            goals.sort(key=lambda g: g.priority)
            return GoalPlan(goals=goals)

        except Exception as e:
            import traceback
            logger.warning(f"[GoalPlanner] LLM goal generation failed: {traceback.format_exc()}, falling back to rules")
            return self._generate_goals_rule_based(instruction, root_causes)

    def _generate_goals_rule_based(self, instruction: str, root_causes: list[str], current_state=None) -> GoalPlan:
        """Generate goals using rule-based mapping.

        Also parses instruction keywords to generate appropriate goals.
        """
        goals = []

        # Add goals based on root causes
        for cause in root_causes:
            cause_goals = self.ROOT_CAUSE_TO_GOALS.get(cause, [])
            goals.extend(cause_goals)

        # Parse instruction keywords to add relevant goals
        instruction_lower = instruction.lower()

        # Check if document is already parsed
        already_parsed = current_state is not None and current_state.page_count > 0

        # "解析" (parse) - document parsing (only if not already parsed)
        if "解析" in instruction or "parse" in instruction_lower:
            if not already_parsed:
                goals.append(Goal(
                    goal_id="parse_document",
                    description="Parse the input document",
                    priority=0,
                    success_criteria=["document_parsed"],
                    skill_name="parse_skill",
                    params={},
                ))

        # "质量检测" or "质量" (quality check/validation)
        if "质量" in instruction or "quality" in instruction_lower or "验证" in instruction:
            goals.append(Goal(
                goal_id="validate_quality",
                description="Validate output quality",
                priority=1,
                success_criteria=["validation passed"],
                skill_name="validate_skill",
                params={},
            ))

        # "空白页" (empty pages) - need to handle empty pages
        if "空白页" in instruction or "empty" in instruction_lower:
            goals.append(Goal(
                goal_id="handle_empty_pages",
                description="Handle or remove empty pages",
                priority=2,
                success_criteria=["empty_pages_ratio < 0.1"],
                skill_name="remove_empty_pages_skill",
                params={},
            ))

        # "重新解析" (re-parse) - reparse bad pages
        if "重新解析" in instruction or "repars" in instruction_lower:
            goals.append(Goal(
                goal_id="reparse_bad_pages",
                description="Re-parse pages with quality issues",
                priority=2,
                success_criteria=["pages_reparsed"],
                skill_name="reparse_bad_pages_skill",
                params={},
            ))

        # "文档元素树" or "文档树" (document element tree)
        if "文档元素树" in instruction or "文档树" in instruction or "doc_tree" in instruction_lower:
            goals.append(Goal(
                goal_id="build_doc_tree",
                description="Build semantic document tree with chapter/section hierarchy",
                priority=3,
                success_criteria=["document_tree_generated"],
                skill_name="doc_tree_skill",
                params={},
            ))

        # If no goals were generated, add default validation
        if not goals:
            goals.append(Goal(
                goal_id="validate_output",
                description="Validate the final output quality",
                priority=0,
                success_criteria=["validation passed"],
            ))

        goals.sort(key=lambda g: g.priority)

        return GoalPlan(goals=goals)

    async def generate_initial_goals(
        self,
        current_state: "RuntimeState",
    ) -> GoalPlan:
        """Generate initial goals for a new task."""
        goals = [
            Goal(
                goal_id="parse_document",
                description="Parse the input document",
                priority=0,
                success_criteria=["document_parsed"],
            ),
            Goal(
                goal_id="validate_quality",
                description="Validate output quality",
                priority=1,
                success_criteria=["validation passed"],
            ),
        ]

        return GoalPlan(goals=goals)


# =============================================================================
# Step-based Planning (original LLM-driven planner)
# =============================================================================


class ExecutionStrategy(Enum):
    """Execution strategy for subtasks."""

    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    CONDITIONAL = "conditional"
    PIPELINE = "pipeline"


class ConditionOperator(Enum):
    """Operators for conditional dependencies."""

    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"
    CONTAINS = "contains"
    EXISTS = "exists"
    NOT_EXISTS = "not_exists"


@dataclass
class TaskCondition:
    """Condition for conditional execution."""

    source_task: str
    field_path: str
    operator: ConditionOperator
    expected_value: Any = None


@dataclass
class ExecutionPlan:
    """Complete execution plan with dependencies and conditions."""

    task_id: str
    subtasks: list[SubTask]
    conditions: dict[str, list[TaskCondition]] = field(default_factory=dict)
    strategy: ExecutionStrategy = ExecutionStrategy.SEQUENTIAL
    retry_policy: dict = field(default_factory=dict)
    timeout_seconds: int = 300


@dataclass
class PlannedStep:
    """A single planned step in the execution."""

    step_id: str
    agent_name: str
    action: str
    input_mapping: dict
    conditions: list[TaskCondition] = field(default_factory=list)
    on_success: str = None  # next step on success
    on_failure: str = None  # next step on failure


class LLMTaskPlanner:
    """LLM-driven task planner."""

    def __init__(
        self,
        llm_client: Optional[BaseLLMClient] = None,
        enable_llm: bool = True,
    ):
        if not llm_client:
            raise ValueError("llm_client is required")
        self.llm_client = llm_client
        self.enable_llm = enable_llm

    async def plan(
        self, instruction: str, input_data: Any
    ) -> ExecutionPlan:
        """Create execution plan from instruction."""
        result = await self.llm_client.plan_tasks(instruction, input_data)
        return self._build_execution_plan(result, input_data)

    def _build_execution_plan(
        self, planning_result: dict, input_data: Any
    ) -> ExecutionPlan:
        """Build ExecutionPlan from LLM output."""
        steps = planning_result.get("steps", [])
        subtasks = []

        for step in steps:
            subtask = SubTask(
                subtask_id=step.get("step_id", f"task_{len(subtasks)}"),
                agent_name=step.get("agent_name", "document_parser"),
                skill_requirements=self._get_skills_for_agent(
                    step.get("agent_name", "document_parser")
                ),
                input_data=step.get("input_mapping", {}),
                dependencies=[],  # Will be set based on input_mapping
                retry_config={"max_attempts": 3, "base_delay": 1.0},
            )
            subtasks.append(subtask)

        # Set dependencies based on input_mapping references
        subtasks = self._resolve_dependencies(subtasks)

        # Validate strategy, fallback to PIPELINE if invalid
        strategy_value = planning_result.get("strategy", "pipeline")
        try:
            strategy = ExecutionStrategy(strategy_value)
        except ValueError:
            logger.warning(f"Invalid strategy '{strategy_value}', using 'pipeline'")
            strategy = ExecutionStrategy.PIPELINE

        return ExecutionPlan(
            task_id=f"plan_{len(subtasks)}",
            subtasks=subtasks,
            strategy=strategy,
            retry_policy={"max_attempts": 3},
            timeout_seconds=300,
        )

    def _get_skills_for_agent(self, agent_name: str) -> list[str]:
        """Get required skills for an agent."""
        skills_map = {
            "document_parser": ["parse_skill"],
            "quality_validator": ["filter_skill"],
            "structure_processor": ["format_skill"],
        }
        return skills_map.get(agent_name, ["parse_skill"])

    def _resolve_dependencies(self, subtasks: list[SubTask]) -> list[SubTask]:
        """Resolve dependencies based on input mapping references."""
        id_to_subtask = {st.subtask_id: st for st in subtasks}

        for subtask in subtasks:
            input_data = subtask.input_data
            if isinstance(input_data, dict):
                for key, value in input_data.items():
                    if isinstance(value, str) and value.startswith("step_"):
                        if value not in subtask.dependencies:
                            subtask.dependencies.append(value)

        return subtasks
