# Copyright (c) Data Agent Team. All rights reserved.
"""Main Agent - 核心流程: main-agent -> (plan编排) -> (parse -> quality)"""

import asyncio
from datetime import datetime
from dataclasses import dataclass, field
from typing import Any, Optional

from loguru import logger

from data_agent.agent.base import AgentContext, AgentResponse, AgentStatus, BaseAgent
from data_agent.agent.planner import LLMTaskPlanner, ExecutionPlan, GoalPlanner, GoalPlan
from data_agent.agent.resource_monitor import ResourceMonitor, CircuitBreaker
from data_agent.agent.action_selector import ActionSelector
from data_agent.agent.recovery_planner import RecoveryPlanner
from data_agent.agent.reflection_agent import ReflectionAgent
from data_agent.agent.execution_loop import ActionExecutionLoop
from data_agent.state.runtime_state import RuntimeState, MiddleJson, ProcessingStatus
from data_agent.utils.task import SubTask


@dataclass
class StepResult:
    """Step execution result."""
    step_id: str
    success: bool
    output: Any = None
    error: str = None
    start_time: datetime = field(default_factory=datetime.utcnow)
    end_time: Any = None
    execution_time_ms: float = 0


class MainAgent(BaseAgent):
    """Main Agent - 使用 LLM Planner 编排 parse -> quality 流程

    支持两种模式:
    1. DEFAULT 模式: 使用 LLMTaskPlanner 生成固定步骤列表，一次执行
    2. AUTONOMOUS 模式: 使用 GoalPlanner 生成目标，ReflectionAgent 驱动 Observe→Reflect→Replan→Act 循环
    """

    def __init__(
        self,
        sub_agents: dict,
        skill_registry=None,
        resource_monitor: Optional[ResourceMonitor] = None,
        llm_planner: Optional[LLMTaskPlanner] = None,
        enable_autonomous: bool = True,
        autonomous_max_iterations: int = 2,
        logs_dir: str = "logs",
    ):
        """
        初始化 MainAgent.

        Args:
            sub_agents: 子代理字典 {agent_name: agent_instance}
            skill_registry: 技能注册表
            resource_monitor: 资源监控器
            llm_planner: LLM 任务规划器
            enable_autonomous: 是否启用自主模式 (Observe→Reflect→Replan→Act)
            autonomous_max_iterations: 自主模式最大迭代次数
            logs_dir: 日志目录
        """
        super().__init__("MainAgent")
        self.sub_agents = sub_agents
        self.skill_registry = skill_registry
        self.resource_monitor = resource_monitor or ResourceMonitor()
        self.llm_planner = llm_planner
        self.enable_autonomous = enable_autonomous
        self.autonomous_max_iterations = autonomous_max_iterations
        self.logs_dir = logs_dir
        self._circuit_breakers: dict[str, CircuitBreaker] = {}
        self._timeline_logger = None
        self._file_logger = None
        self._dag = None
        self._filename = None

        if enable_autonomous:
            self._init_autonomous_components()

    def _init_autonomous_components(self) -> None:
        """初始化自主模式组件"""
        from data_agent.skills import (
            SkillRegistry, SkillConfig,
            ParseSkill, FormatSkill, FilterSkill,
            RemoveEmptyPagesSkill, RepairTableSkill, LowerScoreSkill,
            SwitchBackendSkill, ValidateSkill, ReparseBadPagesSkill,
            MinerUParseSkill, DocTreeSkill,
        )
        from data_agent.skills.registry import SkillMetadata

        if self.skill_registry is None:
            self.skill_registry = SkillRegistry()

        self._register_autonomous_skills()

        llm_planner_client = None
        if self.llm_planner and hasattr(self.llm_planner, 'llm_client'):
            llm_planner_client = self.llm_planner.llm_client

        self.goal_planner = GoalPlanner(llm_client=llm_planner_client, skill_registry=self.skill_registry)
        # Note: timeline_logger will be set in _execute_autonomous after it's created
        self.action_selector = ActionSelector(skill_registry=self.skill_registry)
        self.recovery_planner = RecoveryPlanner()
        self.reflection_agent = ReflectionAgent(
            skill_registry=self.skill_registry,
            goal_planner=self.goal_planner,
            action_selector=self.action_selector,
            recovery_planner=self.recovery_planner,
            max_iterations=self.autonomous_max_iterations,
        )
        self.execution_loop = ActionExecutionLoop(
            reflection_agent=self.reflection_agent,
            action_selector=self.action_selector,
            recovery_planner=self.recovery_planner,
            skill_registry=self.skill_registry,
            max_iterations=self.autonomous_max_iterations,
            timeline_logger=self._timeline_logger,
            file_logger=self._file_logger,
            dag=self._dag,
        )
        logger.info("[MainAgent] Autonomous components initialized")

    def _register_autonomous_skills(self) -> None:
        """注册自主模式所需的技能"""
        from data_agent.skills import SkillConfig
        from data_agent.skills.registry import SkillMetadata

        skills_to_register = [
            ("parse_skill", "parse_skill", "Parse documents using MinerU", "parse", [], [], ["no_pages_detected"], {}, {"path": "文件路径"}),
            ("validate_skill", "validate_skill", "Validate middle_json quality", "validate", ["has_content"], ["validation_complete"], ["no_pages_detected", "empty_page", "high_discarded_ratio", "low_text_content"], {}, {}),
            ("filter_skill", "filter_skill", "Filter and deduplicate blocks", "filter", ["has_content"], [], ["high_discarded_ratio"], {}, {}),
            ("format_skill", "format_skill", "Format output structure", "format", ["has_content"], [], [], {}, {}),
            ("remove_empty_pages_skill", "remove_empty_pages_skill", "Remove empty pages from output", "cleanup", ["has_empty_pages"], [], ["empty_page"], {}, {}),
            ("repair_table_skill", "repair_table_skill", "Repair table structure issues", "repair", ["has_tables"], [], ["table_structure_invalid"], {}, {}),
            ("lower_score_skill", "lower_score_skill", "Lower threshold to keep more blocks", "repair", ["high_discarded_ratio"], [], ["high_discarded_ratio"], {}, {}),
            ("switch_backend_skill", "switch_backend_skill", "Switch to different parsing backend", "repair", ["validation_failed"], [], ["low_text_content", "table_structure_invalid"], {}, {}),
            ("reparse_bad_pages_skill", "reparse_bad_pages_skill", "Re-parse bad quality pages and merge", "repair", ["has_bad_pages"], [], ["no_pages_detected", "low_text_content"], {}, {"file_path": "文件路径", "backend": "解析后端"}),
            ("mineru_parse_skill", "mineru_parse_skill", "Parse document via MinerU API", "parse", [], [], ["no_pages_detected"], {}, {"path": "文件路径", "backend": "解析后端(pipeline/hybrid/vlm-auto-engine)"}),
            ("doc_tree_skill", "doc_tree_skill", "Build semantic document tree (chapters/sections/paragraphs) from middle_json", "structure", ["has_content"], [], ["needs_doc_tree"], {}, {}),
        ]

        for skill_name, reg_name, desc, category, precons, postcons, addresses, default_params, param_descs in skills_to_register:
            skill_config = SkillConfig(name=reg_name)
            metadata = SkillMetadata(
                description=desc,
                category=category,
                preconditions=precons,
                postconditions=postcons,
                addresses_issues=addresses,
                default_params=default_params,
                param_descriptions=param_descs,
            )

            if skill_name == "parse_skill":
                from data_agent.skills import ParseSkill
                skill = ParseSkill(skill_config)
            elif skill_name == "validate_skill":
                from data_agent.skills import ValidateSkill
                skill = ValidateSkill(skill_config)
            elif skill_name == "filter_skill":
                from data_agent.skills import FilterSkill
                skill = FilterSkill(skill_config)
            elif skill_name == "format_skill":
                from data_agent.skills import FormatSkill
                skill = FormatSkill(skill_config)
            elif skill_name == "remove_empty_pages_skill":
                from data_agent.skills import RemoveEmptyPagesSkill
                skill = RemoveEmptyPagesSkill(skill_config)
            elif skill_name == "repair_table_skill":
                from data_agent.skills import RepairTableSkill
                skill = RepairTableSkill(skill_config)
            elif skill_name == "lower_score_skill":
                from data_agent.skills import LowerScoreSkill
                skill = LowerScoreSkill(skill_config)
            elif skill_name == "switch_backend_skill":
                from data_agent.skills import SwitchBackendSkill
                skill = SwitchBackendSkill(skill_config)
            elif skill_name == "reparse_bad_pages_skill":
                from data_agent.skills import ReparseBadPagesSkill
                skill = ReparseBadPagesSkill(skill_config)
            elif skill_name == "mineru_parse_skill":
                from data_agent.skills import MinerUParseSkill
                skill = MinerUParseSkill(skill_config)
            elif skill_name == "doc_tree_skill":
                from data_agent.skills import DocTreeSkill
                skill = DocTreeSkill(skill_config)
            else:
                continue

            skill.action_metadata = {
                "description": desc,
                "category": category,
                "preconditions": precons,
                "postconditions": postcons,
                "addresses_issues": addresses,
            }

            self.skill_registry.register(skill, metadata=metadata)
            logger.debug(f"[MainAgent] Registered skill: {skill_name}")

    async def execute(self, context: AgentContext) -> AgentResponse:
        """执行主流程: plan -> parse -> quality

        根据 enable_autonomous 配置选择执行模式:
        - AUTONOMOUS: GoalPlanner + ReflectionAgent (Observe→Reflect→Replan→Act)
        - DEFAULT: LLMTaskPlanner (固定步骤列表)
        """
        try:
            if self.enable_autonomous:
                return await self._execute_autonomous(context)
            else:
                return await self._execute_default(context)
        except Exception as e:
            import traceback
            logger.error(f"Main agent execution failed: {traceback.format_exc()}")
            return AgentResponse(
                success=False,
                status=AgentStatus.FAILED,
                error=str(e),
                context=context,
            )

    async def _execute_autonomous(self, context: AgentContext) -> AgentResponse:
        """自主模式: 使用 GoalPlanner + ReflectionAgent 驱动循环"""
        from data_agent.utils.file_logger import FileLogger
        from data_agent.utils.timeline_logger import TimelineLogger
        from data_agent.utils.execution_dag import ExecutionDAG

        instruction = context.original_input.get("instruction", "")
        raw_data = context.original_input.get("data")

        middle_json_data = None
        path = None
        self._filename = None

        if isinstance(raw_data, dict):
            middle_json_data = raw_data.get("middle_json")
            path = raw_data.get("path")
        elif isinstance(raw_data, list) and len(raw_data) > 0:
            first_item = raw_data[0]
            if isinstance(first_item, dict):
                middle_json_data = first_item.get("middle_json")
                path = first_item.get("path")

        # Compute SHA256 hash early for log filename and output directory
        file_hash = None
        original_filename = "unknown"
        if path:
            from pathlib import Path
            import hashlib

            source_path_for_hash = Path(path)
            source_filename = source_path_for_hash.name

            # Compute SHA256 hash of file content
            hasher = hashlib.sha256()
            with open(source_path_for_hash, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    hasher.update(chunk)
            file_hash = hasher.hexdigest()

            # Extract original filename (remove UUID prefix if present)
            # UUID format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx (36 chars with 4 dashes)
            parts = source_filename.split("_", 1)
            if len(parts) > 1:
                uuid_part = parts[0]
                # Check if it looks like a UUID: 8-4-4-4-12 pattern
                uuid_pattern = uuid_part.count("-") == 4 and len(uuid_part) == 36
                if uuid_pattern:
                    original_filename = parts[1]
                else:
                    original_filename = source_filename
            else:
                original_filename = source_filename

        # Initialize loggers with filename using SHA256 format (suppress other logs ASAP)
        self._filename = f"{file_hash[:16]}-{original_filename}" if file_hash else original_filename

        self._file_logger = FileLogger(context.task_id, self._filename, self.logs_dir)
        self._file_logger.create_subdirs()
        self._timeline_logger = TimelineLogger(context.task_id, self._filename)
        self._timeline_logger.suppress_other_logs()  # suppress BEFORE any other logs
        self._dag = ExecutionDAG(context.task_id)

        # Set timeline_logger to goal_planner (created in _init_autonomous_components before this method)
        if hasattr(self, 'goal_planner') and self.goal_planner:
            self.goal_planner.set_timeline_logger(self._timeline_logger)

        # Set loggers to execution_loop after initialization
        self.execution_loop.set_loggers(
            timeline_logger=self._timeline_logger,
            file_logger=self._file_logger,
            dag=self._dag,
        )

        # Write request.json
        self._file_logger.write_request({
            "task_id": context.task_id,
            "filename": self._filename,
            "instruction": instruction,
            "input": context.original_input,
        })

        # Timeline: Task start
        self._timeline_logger.running(
            "MainAgent",
            f"Starting task: {instruction[:50]}..." if len(instruction) > 50 else f"Starting task: {instruction}"
        )

        if middle_json_data:
            source_file = middle_json_data.get("_file_path") or path
            # Convert dict to MiddleJson if needed
            if isinstance(middle_json_data, dict):
                middle_json_obj = MiddleJson.model_validate(middle_json_data)
            else:
                middle_json_obj = middle_json_data
            # Use GoalPlanner to generate goals based on instruction
            initial_state = RuntimeState(
                task_id=context.task_id,
                middle_json=middle_json_obj,
                current_goals=[],
                original_instruction=instruction,
                source_file=source_file,
            )
            goal_plan = await self.goal_planner.generate_goals(
                instruction=instruction,
                current_state=initial_state,
                root_causes=[],
            )
            runtime_state = RuntimeState(
                task_id=context.task_id,
                middle_json=middle_json_obj,
                current_goals=[g.skill_name for g in goal_plan.goals] if goal_plan.goals else ["validate_quality"],
                original_instruction=instruction,
                source_file=source_file,
            )
        elif path:
            # Use already-computed file_hash and original_filename from earlier
            output_base = Path("./output") / f"{file_hash[:16]}-{original_filename}"
            mineru_parse_dir = output_base / "mineru_parse"
            mineru_parse_dir.mkdir(parents=True, exist_ok=True)

            document_parser = self.sub_agents.get("document_parser") if self.sub_agents else None
            if not document_parser:
                return AgentResponse(
                    success=False,
                    status=AgentStatus.FAILED,
                    error="DocumentParser sub-agent not configured",
                    context=context,
                )

            # Set local mineru_parse output directory for cache lookup
            if hasattr(document_parser, '_miner_parse_output_dir'):
                document_parser._miner_parse_output_dir = str(mineru_parse_dir)

            logger.info(f"[MainAgent][AUTONOMOUS] Parsing document with DocumentParser: {path}")
            parse_context = AgentContext(
                task_id=context.task_id,
                original_input={"path": path},
                timeline_logger=self._timeline_logger,
            )
            parse_response = await document_parser.execute(parse_context)

            if not parse_response.success:
                return AgentResponse(
                    success=False,
                    status=AgentStatus.FAILED,
                    error=f"Document parsing failed: {parse_response.error}",
                    context=context,
                )

            middle_json_data = parse_response.output.get("middle_json", {})
            parse_metadata = parse_response.output.get("metadata", {})
            logger.info(f"[MainAgent][AUTONOMOUS] Parsed {len(middle_json_data.get('pdf_info', []))} pages")

            source_file = parse_metadata.get("file_path") or path
            cache_path = parse_metadata.get("cache_path")  # Get cache path if available
            if source_file:
                middle_json_data["_file_path"] = source_file

            # Convert dict to MiddleJson if needed
            if isinstance(middle_json_data, dict):
                middle_json_obj = MiddleJson.model_validate(middle_json_data)
            else:
                middle_json_obj = middle_json_data

            # Use GoalPlanner to generate goals based on instruction
            initial_state = RuntimeState(
                task_id=context.task_id,
                middle_json=middle_json_obj,
                current_goals=[],
                original_instruction=instruction,
                source_file=source_file,
            )
            goal_plan = await self.goal_planner.generate_goals(
                instruction=instruction,
                current_state=initial_state,
                root_causes=[],
            )
            runtime_state = RuntimeState(
                task_id=context.task_id,
                middle_json=middle_json_obj,
                current_goals=[g.skill_name for g in goal_plan.goals] if goal_plan.goals else ["validate_quality"],
                original_instruction=instruction,
                source_file=source_file,
            )
            if cache_path:
                runtime_state.metadata["miner_parse_cache_path"] = cache_path
        else:
            return AgentResponse(
                success=False,
                status=AgentStatus.FAILED,
                error="No path or middle_json provided",
                context=context,
            )

        try:
            # Determine output directory structure
            # Format: ./output/{uuid}-{original_filename}/
            source_file = runtime_state.get_source_file() if hasattr(runtime_state, 'get_source_file') else None
            output_base = None
            origin_dir = None
            mineru_parse_dir = None
            reflection_output_dir = None
            documenttree_output_dir = None

            if source_file and source_file != "N/A":
                from pathlib import Path
                import shutil
                import hashlib

                source_path = Path(source_file)
                source_filename = source_path.name

                # Compute SHA256 hash of file content for cache key
                hasher = hashlib.sha256()
                with open(source_path, "rb") as f:
                    for chunk in iter(lambda: f.read(8192), b""):
                        hasher.update(chunk)
                file_hash = hasher.hexdigest()

                # Extract original filename (remove UUID prefix if present)
                # UUID format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx (36 chars with 4 dashes)
                parts = source_filename.split("_", 1)
                if len(parts) > 1:
                    uuid_part = parts[0]
                    # Check if it looks like a UUID: 8-4-4-4-12 pattern
                    uuid_pattern = uuid_part.count("-") == 4 and len(uuid_part) == 36
                    if uuid_pattern:
                        original_filename = parts[1]
                    else:
                        original_filename = source_filename
                else:
                    original_filename = source_filename

                # Use SHA256 hash as directory name key
                output_base = Path("./output") / f"{file_hash[:16]}-{original_filename}"
                origin_dir = output_base / "origin"
                mineru_parse_dir = output_base / "mineru_parse"
                reflection_output_dir = output_base / "reflection_output"
                documenttree_output_dir = output_base / "documenttree_output"

                # Create directories
                for d in [origin_dir, mineru_parse_dir, reflection_output_dir, documenttree_output_dir]:
                    d.mkdir(parents=True, exist_ok=True)

                # Copy original file to origin/ (use clean filename without UUID prefix)
                if source_path.exists():
                    shutil.copy(source_path, origin_dir / original_filename)

                # Copy MinerU cache to mineru_parse/
                miner_parse_cache_path = runtime_state.metadata.get("miner_parse_cache_path")
                if miner_parse_cache_path and Path(miner_parse_cache_path).exists():
                    cache_dest = mineru_parse_dir / "middle.json"
                    shutil.copy(miner_parse_cache_path, cache_dest)
                    logger.info(f"[MainAgent][AUTONOMOUS] MinerU cache copied to {cache_dest}")

                logger.info(f"[MainAgent][AUTONOMOUS] Output directory: {output_base}")

            # Run reflection-agent loop (handles parsing, validation, etc.)
            final_state = await self.execution_loop.run(runtime_state)

            if final_state is None:
                logger.error("[MainAgent][AUTONOMOUS] execution_loop.run returned None")
                return AgentResponse(
                    success=False,
                    status=AgentStatus.FAILED,
                    error="Execution loop returned None",
                    context=context,
                )

            # Save reflection output (middle_json after reflection-agent loop)
            if reflection_output_dir:
                import json
                reflection_json_out = reflection_output_dir / "middle.json"
                with open(reflection_json_out, 'w', encoding='utf-8') as f:
                    json.dump(final_state.middle_json.model_dump(), f, ensure_ascii=False, indent=2)
                logger.info(f"[MainAgent][AUTONOMOUS] reflection output saved to {reflection_json_out}")

            # After reflection-agent loop completes, check if doc_tree goal was requested
            doc_tree_goal_requested = "doc_tree_skill" in [g.skill_name for g in goal_plan.goals] if goal_plan.goals else False

            if doc_tree_goal_requested:
                # Call document-tree sub-agent ONCE, separate from reflection loop
                logger.info("[MainAgent][AUTONOMOUS] Calling document-tree sub-agent for final doc tree generation")
                from data_agent.agent.sub_agents.document_tree.builder import DocumentTreeBuilder

                builder = DocumentTreeBuilder(skill_registry=self.skill_registry, config=None)
                builder_context = AgentContext(
                    task_id=context.task_id,
                    original_input={"middle_json": final_state.middle_json.model_dump()},
                    timeline_logger=self._timeline_logger,
                )
                doc_tree_response = await builder.execute(builder_context)

                if doc_tree_response.success:
                    # Merge doc_tree results back into middle_json for output
                    doc_tree_output = doc_tree_response.output or {}
                    # Use model_dump to get dict, update it, then recreate MiddleJson
                    # to avoid "MiddleJson does not support item assignment" error
                    middle_dump = final_state.middle_json.model_dump()
                    middle_dump["document_tree"] = doc_tree_output.get("document_tree", {})
                    middle_dump["layout_graph"] = doc_tree_output.get("layout_graph", {})
                    middle_dump["txt_tree"] = doc_tree_output.get("txt_tree", "")
                    middle_dump["doc_tree_stats"] = doc_tree_output.get("stats", {})
                    file_path = getattr(final_state.middle_json, '_file_path', None)
                    final_state.middle_json = MiddleJson(**middle_dump)
                    if file_path:
                        final_state.middle_json._file_path = file_path

                    # Save doc_tree results to documenttree_output/
                    if documenttree_output_dir:
                        import json
                        doc_tree_json_out = documenttree_output_dir / "document_tree.json"
                        doc_tree_txt_out = documenttree_output_dir / "document_tree.txt"

                        with open(doc_tree_json_out, 'w', encoding='utf-8') as f:
                            json.dump(doc_tree_response.output.get("document_tree", {}), f, ensure_ascii=False, indent=2)

                        with open(doc_tree_txt_out, 'w', encoding='utf-8') as f:
                            f.write(doc_tree_response.output.get("txt_tree", ""))

                        logger.info(f"[MainAgent][AUTONOMOUS] doc_tree saved to {doc_tree_json_out}")
                    final_state.document_tree_generated = True
                else:
                    logger.warning(f"[MainAgent][AUTONOMOUS] document-tree sub-agent failed: {doc_tree_response.error}")

            # Save timeline logs to output directory (in same format as terminal output)
            # Log file: output/sha256-原文件名/sha256-原文件名.log
            if output_base:
                log_file_path = output_base / f"{output_base.name}.log"
                log_text = self._timeline_logger.get_logs_text()
                with open(log_file_path, "w", encoding="utf-8") as f:
                    f.write(log_text + "\n")
                logger.info(f"[MainAgent][AUTONOMOUS] Timeline logs saved to {log_file_path}")

                # Copy original logs directory to output/sha256-原文件名/logs/
                logs_src = self._file_logger.get_task_dir()
                logs_dest = output_base / "logs"
                if Path(logs_src).exists():
                    import shutil
                    if logs_dest.exists():
                        shutil.rmtree(logs_dest)
                    shutil.copytree(logs_src, logs_dest)
                    logger.info(f"[MainAgent][AUTONOMOUS] Original logs copied to {logs_dest}")

            if final_state.status == ProcessingStatus.COMPLETED:
                logger.info(f"[MainAgent][AUTONOMOUS] Completed successfully")
                return AgentResponse(
                    success=True,
                    status=AgentStatus.COMPLETED,
                    output={
                        "status": "completed",
                        "middle_json": final_state.middle_json.model_dump(),
                        "completed_goals": final_state.completed_goals,
                        "validation_history": final_state.validation_history,
                    },
                    context=context,
                )
            else:
                # Check if doc_tree was built successfully even if status is not COMPLETED
                middle_dump = final_state.middle_json.model_dump()
                has_doc_tree = middle_dump.get("document_tree") or middle_dump.get("txt_tree")
                if has_doc_tree:
                    logger.info(f"[MainAgent][AUTONOMOUS] Ended with status {final_state.status.value} but doc_tree exists - treating as success")
                    return AgentResponse(
                        success=True,
                        status=AgentStatus.COMPLETED,
                        output={
                            "status": "completed",
                            "middle_json": final_state.middle_json.model_dump(),
                            "completed_goals": final_state.completed_goals,
                            "validation_history": final_state.validation_history,
                        },
                        context=context,
                    )
                logger.warning(f"[MainAgent][AUTONOMOUS] Ended with status: {final_state.status}")
                return AgentResponse(
                    success=False,
                    status=AgentStatus.FAILED,
                    error=f"Autonomous loop ended with status: {final_state.status.value}",
                    output={
                        "middle_json": final_state.middle_json.model_dump(),
                        "failed_goals": final_state.failed_goals,
                        "recovery_attempts": final_state.recovery_attempts,
                    },
                    context=context,
                )
        except Exception as e:
            import traceback
            logger.error(f"[MainAgent][AUTONOMOUS] Execution failed: {traceback.format_exc()}")
            return AgentResponse(
                success=False,
                status=AgentStatus.FAILED,
                error=str(e),
                context=context,
            )

    async def _execute_default(self, context: AgentContext) -> AgentResponse:
        """默认模式: 使用 LLMTaskPlanner 生成固定步骤列表"""
        instruction = context.original_input.get("instruction", "")
        input_data = context.original_input.get("data")

        if input_data is None:
            return AgentResponse(
                success=False,
                status=AgentStatus.FAILED,
                error="No input data provided",
                context=context,
            )

        plan = await self.llm_planner.plan(instruction, input_data)
        logger.info(f"Plan: {len(plan.subtasks)} subtasks, strategy={plan.strategy.value}")

        results = await self._execute_plan(plan, context)
        aggregated = self._aggregate_results(results)

        return AgentResponse(
            success=True,
            status=AgentStatus.COMPLETED,
            output=aggregated,
            context=context,
        )

    async def _execute_plan(
        self,
        plan: ExecutionPlan,
        context: AgentContext,
    ) -> dict[str, Any]:
        """执行 ExecutionPlan"""
        results = {}
        completed = set()
        pending = {t.subtask_id: t for t in plan.subtasks}

        while pending:
            # 找到依赖已满足的 tasks
            ready = [t for t in pending.values() if all(d in completed for d in t.dependencies)]
            if not ready:
                raise ValueError(f"Circular dependency: {pending.keys() - completed}")

            # 执行 batch
            batch = ready[:4]
            logger.info(f"Executing {len(batch)} tasks")

            batch_results = await asyncio.gather(
                *[self._execute_subtask(t, context, results) for t in batch],
                return_exceptions=True,
            )

            for subtask, result in zip(batch, batch_results):
                if isinstance(result, Exception):
                    import traceback
                    logger.error(f"Subtask {subtask.subtask_id} failed: {traceback.format_exc()}")
                    results[subtask.subtask_id] = None
                else:
                    subtask_id, output = result
                    results[subtask_id] = output
                completed.add(subtask.subtask_id)
                del pending[subtask.subtask_id]

        return results

    async def _execute_subtask(
        self,
        subtask: SubTask,
        context: AgentContext,
        results: dict,
    ) -> tuple[str, Any]:
        """执行单个 subtask"""
        agent = self.sub_agents.get(subtask.agent_name)
        if not agent:
            return subtask.subtask_id, {"error": f"Unknown agent: {subtask.agent_name}"}

        if not await self._can_execute(subtask.agent_name):
            return subtask.subtask_id, {"error": f"Circuit breaker open", "skipped": True}

        # 创建 subtask context
        subtask_context = AgentContext(
            task_id=context.task_id,
            original_input=subtask.input_data,
            current_state=context.current_state.copy(),
            metadata={"subtask_id": subtask.subtask_id},
        )

        # 为 quality_validator 注入 middle_json
        if subtask.agent_name == "quality_validator":
            # LLM planner outputs "source_step", SimpleTaskPlanner outputs "source_subtask"
            source_id = subtask.input_data.get("source_step") or subtask.input_data.get("source_subtask")
            if source_id and source_id in results and results[source_id]:
                parse_result = results[source_id]
                if isinstance(parse_result, dict):
                    subtask_context.original_input["middle_json"] = parse_result.get("middle_json", parse_result)

        # 为 recovery_executor 注入 validation_result 和 middle_json
        if subtask.agent_name == "recovery_executor":
            source_id = subtask.input_data.get("source_step") or subtask.input_data.get("source_subtask")
            if source_id and source_id in results and results[source_id]:
                val_result = results[source_id]
                if isinstance(val_result, dict):
                    subtask_context.original_input["validation_result"] = val_result
                    subtask_context.original_input["middle_json"] = val_result.get("middle_json", val_result)

        try:
            await self.resource_monitor.task_started()
            response = await agent.execute(subtask_context)
            await self.resource_monitor.task_completed()

            if response.success:
                await self._record_success(subtask.agent_name)
                return subtask.subtask_id, response.output
            else:
                await self._record_failure(subtask.agent_name)
                return subtask.subtask_id, {"error": response.error}
        except Exception as e:
            import traceback
            logger.error(f"Subtask {subtask.subtask_id} failed: {traceback.format_exc()}")
            await self._record_failure(subtask.agent_name)
            return subtask.subtask_id, {"error": str(e)}

    async def _can_execute(self, agent_name: str) -> bool:
        if agent_name not in self._circuit_breakers:
            self._circuit_breakers[agent_name] = CircuitBreaker()
        return await self._circuit_breakers[agent_name].can_execute()

    async def _record_success(self, agent_name: str) -> None:
        if agent_name in self._circuit_breakers:
            await self._circuit_breakers[agent_name].record_success()

    async def _record_failure(self, agent_name: str) -> None:
        if agent_name in self._circuit_breakers:
            await self._circuit_breakers[agent_name].record_failure()

    def _aggregate_results(self, results: dict) -> dict:
        """聚合结果"""
        successful = [r for r in results.values() if isinstance(r, dict) and not r.get("error")]

        merged = {"pdf_info": [], "_backend": "multi", "_version_name": "1.0.0"}
        for r in successful:
            if isinstance(r, dict) and "middle_json" in r:
                mj = r["middle_json"]
                if "pdf_info" in mj:
                    merged["pdf_info"].extend(mj["pdf_info"])

        return {
            "status": "completed",
            "results_count": len(successful),
            "aggregated_output": merged,
        }