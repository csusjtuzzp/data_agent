# Copyright (c) Data Agent Team. All rights reserved.
"""DocTreeSkill - Build semantic document tree from middle_json via DocumentTreeBuilder."""

from typing import Any

from data_agent.skills.base_skill import BaseSkill, SkillConfig
from loguru import logger

from data_agent.agent.sub_agents.document_tree.builder import DocumentTreeBuilder


class DocTreeSkill(BaseSkill):
    """Skill for building a semantic document tree from middle_json.

    This skill wraps DocumentTreeBuilder (6-stage pipeline) and exposes it
    as a SkillRegistry-compatible skill for autonomous GoalPlanner selection.

    Input:  middle_json dict with pdf_info (from MinerU parse result)
    Output: dict containing:
        - document_tree: serialized DocumentTree (node_id -> DocNode dict)
        - layout_graph: serialized LayoutGraph {node_count, edge_count, nodes}
        - txt_tree: ASCII tree visualization string
        - stats: {page_count, layout_node_count, doc_node_count, table_group_count}
    """

    def __init__(self, config: SkillConfig):
        super().__init__(config)
        self._builder = DocumentTreeBuilder(skill_registry=None, config=None)

    async def execute(self, data: Any, **kwargs) -> Any:
        """Execute the document tree building pipeline."""
        if not self.validate_input(data):
            logger.warning("[DocTreeSkill] Invalid input: missing pdf_info")
            return data if isinstance(data, dict) else {}

        middle_json = data if isinstance(data, dict) else {}

        # DocumentTreeBuilder.execute() expects an AgentContext, so we build a minimal one
        from data_agent.agent.base import AgentContext
        context = AgentContext(
            task_id="doc_tree_skill",
            original_input={"middle_json": middle_json},
        )

        response = await self._builder.execute(context)

        if not response.success:
            logger.error(f"[DocTreeSkill] DocumentTreeBuilder failed: {response.error}")
            return middle_json

        # Merge output back into middle_json
        result = response.output
        middle_json["document_tree"] = result.get("document_tree", {})
        middle_json["layout_graph"] = result.get("layout_graph", {})
        middle_json["txt_tree"] = result.get("txt_tree", "")
        middle_json["doc_tree_stats"] = result.get("stats", {})

        logger.info(
            f"[DocTreeSkill] Built doc tree: {result.get('stats', {}).get('doc_node_count', 0)} nodes, "
            f"{result.get('stats', {}).get('table_group_count', 0)} table groups"
        )

        return middle_json

    def validate_input(self, data: Any) -> bool:
        """Validate that middle_json contains pdf_info."""
        if isinstance(data, dict):
            return "pdf_info" in data and len(data.get("pdf_info", [])) > 0
        return False