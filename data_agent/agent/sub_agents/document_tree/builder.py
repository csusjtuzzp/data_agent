# Copyright (c) Data Agent Team. All rights reserved.
"""DocumentTreeBuilder sub-agent - transforms middle_json to semantic Document Tree."""

from typing import Any, Optional

from data_agent.agent.base import AgentContext, AgentResponse, AgentStatus
from data_agent.agent.sub_agents.base_sub_agent import BaseSubAgent

from data_agent.agent.sub_agents.document_tree.layout_graph import LayoutGraph
from data_agent.agent.sub_agents.document_tree.doc_tree import DocumentTree, DocNodeType
from data_agent.agent.sub_agents.document_tree.pipeline import (
    BlockNormalizeStage,
    ReadingOrderStage,
    SectionHierarchyStage,
    CrossPageMergeStage,
    SemanticGroupingStage,
    TreeConstructionStage,
)


class DocumentTreeBuilder(BaseSubAgent):
    """Sub-agent that builds a semantic Document Tree from middle_json."""

    def __init__(self, skill_registry: Optional[Any] = None, config: dict = None):
        super().__init__("DocumentTreeBuilder", skill_registry, config)

        # Initialize pipeline stages
        self.stages = [
            BlockNormalizeStage(),
            ReadingOrderStage(),
            SectionHierarchyStage(),
            CrossPageMergeStage(),
            SemanticGroupingStage(),
            TreeConstructionStage(),
        ]

    def _timeline_log(self, context: AgentContext, level: str, action: str, status=None, extra: dict = None):
        """Log to timeline_logger if available."""
        tl = context.timeline_logger if context else None
        if not tl:
            return

        msg = action
        if extra:
            extra_str = ", ".join(f"{k}={v}" for k, v in extra.items() if v is not None)
            if extra_str:
                msg = f"{action} | {extra_str}"

        if status == "success":
            tl.success("DocumentTreeBuilder", msg, sub_agent="DocumentTreeBuilder")
        elif status == "error":
            tl.error("DocumentTreeBuilder", msg, sub_agent="DocumentTreeBuilder")
        elif status == "running":
            tl.running("DocumentTreeBuilder", msg, sub_agent="DocumentTreeBuilder")
        else:
            tl.running("DocumentTreeBuilder", msg, sub_agent="DocumentTreeBuilder")

    async def execute(self, context: AgentContext) -> AgentResponse:
        """Execute the Document Tree building pipeline."""
        await self.pre_execute(context)

        self._timeline_log(context, "info", "Starting Document Tree building", status="running")

        try:
            input_data = context.original_input
            middle_json = input_data.get("middle_json")

            if not middle_json:
                self._timeline_log(context, "error", "No middle_json provided", status="error")
                return AgentResponse(
                    success=False,
                    status=AgentStatus.FAILED,
                    error="No middle_json provided",
                    context=context,
                )

            page_count = len(middle_json.get("pdf_info", []))

            # Stage 1: Block normalize
            self._timeline_log(context, "info", f"Stage 1: BlockNormalizeStage, processing {page_count} pages", status="running")
            graph = self.stages[0].process(middle_json)
            self._timeline_log(context, "info", f"Stage 1 complete: {len(graph.nodes)} layout nodes", status="success")

            # Stage 2: Reading order
            self._timeline_log(context, "info", "Stage 2: ReadingOrderStage", status="running")
            graph = self.stages[1].process(graph, page_count)
            self._timeline_log(context, "info", "Stage 2 complete", status="success")

            # Stage 3: Section hierarchy
            self._timeline_log(context, "info", "Stage 3: SectionHierarchyStage", status="running")
            graph = self.stages[2].process(graph)
            heading_count = sum(1 for n in graph.nodes.values() if n.is_heading)
            self._timeline_log(context, "info", f"Stage 3 complete: {heading_count} headings detected", status="success")

            # Stage 4: Cross-page merge
            self._timeline_log(context, "info", "Stage 4: CrossPageMergeStage", status="running")
            graph = self.stages[3].process(graph, middle_json)
            self._timeline_log(context, "info", "Stage 4 complete", status="success")

            # Stage 5: Semantic grouping
            self._timeline_log(context, "info", "Stage 5: SemanticGroupingStage", status="running")
            graph = self.stages[4].process(graph)
            self._timeline_log(context, "info", "Stage 5 complete", status="success")

            # Stage 6: Tree construction
            self._timeline_log(context, "info", "Stage 6: TreeConstructionStage", status="running")
            doc_tree = self.stages[5].process(graph)
            self._timeline_log(context, "info", f"Stage 6 complete: {len(doc_tree.nodes)} doc tree nodes", status="success")

            output = {
                "document_tree": doc_tree.to_dict(),
                "layout_graph": self._serialize_graph(graph),
                "txt_tree": doc_tree.to_txt_tree(),
                "stats": {
                    "page_count": page_count,
                    "layout_node_count": len(graph.nodes),
                    "doc_node_count": len(doc_tree.nodes),
                    "table_group_count": len(doc_tree.get_nodes_by_type(DocNodeType.TABLE_GROUP)),
                },
            }

            self._timeline_log(context, "info", f"Document Tree built successfully: {len(doc_tree.nodes)} nodes, {page_count} pages", status="success")

            return AgentResponse(
                success=True,
                status=AgentStatus.COMPLETED,
                output=output,
                context=context,
            )

        except Exception as e:
            import traceback
            self._timeline_log(context, "error", f"DocumentTreeBuilder failed: {str(e)}", status="error")
            return AgentResponse(
                success=False,
                status=AgentStatus.FAILED,
                error=f"DocumentTreeBuilder failed: {str(e)}\n{traceback.format_exc()}",
                context=context,
            )

    def _serialize_graph(self, graph: LayoutGraph) -> dict:
        """Serialize Layout Graph for output."""
        return {
            "node_count": len(graph.nodes),
            "edge_count": len(graph.edges),
            "nodes": {nid: node.to_dict() for nid, node in graph.nodes.items()}
        }