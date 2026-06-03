# Copyright (c) Data Agent Team. All rights reserved.
"""Unit tests for agents."""

import pytest
from data_agent.agent.base import AgentContext, AgentStatus, AgentResponse
from data_agent.agent.main_agent import TaskDecomposer, TaskOrchestrator, MainAgent
from data_agent.agent.sub_agents import DocumentParser, StructureProcessor, QualityValidator


class MockSubAgent:
    """Mock sub-agent for testing."""

    def __init__(self, name: str, success: bool = True):
        self.name = name
        self.success = success

    async def execute(self, context: AgentContext) -> AgentResponse:
        if self.success:
            return AgentResponse(
                success=True,
                status=AgentStatus.COMPLETED,
                output={"result": "success"},
                context=context,
            )
        else:
            return AgentResponse(
                success=False,
                status=AgentStatus.FAILED,
                error="Mock failure",
                context=context,
            )


@pytest.mark.asyncio
async def test_task_decomposer_basic():
    """Test basic task decomposition."""
    decomposer = TaskDecomposer()

    subtasks = decomposer.decompose("parse the document", {"path": "/test.pdf"})

    assert len(subtasks) > 0
    assert all(s.agent_name == "document_parser" for s in subtasks)


@pytest.mark.asyncio
async def test_task_decomposer_multiple_intentions():
    """Test decomposition with multiple intentions."""
    decomposer = TaskDecomposer()

    subtasks = decomposer.decompose(
        "parse and validate the document",
        [{"path": "/test.pdf"}]
    )

    agent_names = {s.agent_name for s in subtasks}
    assert "document_parser" in agent_names
    assert "quality_validator" in agent_names


@pytest.mark.asyncio
async def test_task_orchestrator_basic():
    """Test basic task orchestration."""
    sub_agents = {
        "document_parser": MockSubAgent("document_parser", success=True),
    }

    orchestrator = TaskOrchestrator(sub_agents=sub_agents)

    from data_agent.utils.task import SubTask

    subtask = SubTask(
        subtask_id="test_1",
        agent_name="document_parser",
        skill_requirements=[],
        input_data={"path": "/test.pdf"},
    )

    context = AgentContext(
        task_id="test_task",
        original_input={"path": "/test.pdf"},
    )

    results = await orchestrator.orchestrate([subtask], context)

    assert results["test_1"]["result"] == "success"


@pytest.mark.asyncio
async def test_task_orchestrator_dependency():
    """Test orchestration with dependencies."""
    sub_agents = {
        "document_parser": MockSubAgent("document_parser", success=True),
        "quality_validator": MockSubAgent("quality_validator", success=True),
    }

    orchestrator = TaskOrchestrator(sub_agents=sub_agents)

    from data_agent.utils.task import SubTask

    parse_task = SubTask(
        subtask_id="parse_1",
        agent_name="document_parser",
        skill_requirements=[],
        input_data={"path": "/test.pdf"},
    )

    validate_task = SubTask(
        subtask_id="validate_1",
        agent_name="quality_validator",
        skill_requirements=[],
        input_data={"source_subtask": "parse_1"},
        dependencies=["parse_1"],
    )

    context = AgentContext(
        task_id="test_task",
        original_input={"path": "/test.pdf"},
    )

    results = await orchestrator.orchestrate([parse_task, validate_task], context)

    assert "parse_1" in results
    assert "validate_1" in results
