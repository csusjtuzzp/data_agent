"""
DynamicTaskGraph - Runtime DAG expansion for task execution.
"""

from pydantic import BaseModel, Field
from typing import Any, Optional, TYPE_CHECKING
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field
import uuid

if TYPE_CHECKING:
    from data_agent.state.runtime_state import RuntimeState


class NodeStatus(Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class NodeType(Enum):
    ACTION = "action"
    GOAL = "goal"
    VALIDATION = "validation"
    RECOVERY = "recovery"
    BRANCH = "branch"
    MERGE = "merge"


@dataclass
class GraphNode:
    """A node in the dynamic task graph."""
    node_id: str
    node_type: NodeType
    label: str
    status: NodeStatus = NodeStatus.PENDING

    skill_name: Optional[str] = None
    params: dict = field(default_factory=dict)

    goal_description: Optional[str] = None

    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Any = None
    error: Optional[str] = None

    depends_on: list[str] = field(default_factory=list)

    expansion_rules: list["ExpansionRule"] = field(default_factory=list)


@dataclass
class ExpansionRule:
    """Rule for runtime graph expansion."""
    condition: str
    expand_with: list[tuple[str, dict]]
    priority: int = 0


@dataclass
class GraphEdge:
    """An edge in the dynamic task graph."""
    edge_id: str
    from_node: str
    to_node: str
    condition: Optional[str] = None


class DynamicTaskGraph:
    """
    Runtime-expanding DAG for task execution.

    Unlike static DAGs, this graph:
    1. Expands nodes at runtime based on conditions
    2. Supports conditional edges
    3. Can add new branches when goals are added
    4. Tracks execution state per node
    """

    def __init__(self, task_id: str):
        self.task_id = task_id
        self.nodes: dict[str, GraphNode] = {}
        self.edges: list[GraphEdge] = []
        self._execution_order: list[str] = []

    def add_node(
        self,
        node_type: NodeType,
        label: str,
        node_id: Optional[str] = None,
        depends_on: Optional[list[str]] = None,
        **kwargs,
    ) -> str:
        """Add a node to the graph."""
        node_id = node_id or f"{node_type.value}_{uuid.uuid4().hex[:8]}"

        node = GraphNode(
            node_id=node_id,
            node_type=node_type,
            label=label,
            depends_on=depends_on or [],
            **kwargs,
        )

        self.nodes[node_id] = node
        return node_id

    def add_edge(
        self,
        from_node: str,
        to_node: str,
        condition: Optional[str] = None,
    ) -> str:
        """Add an edge between nodes."""
        edge_id = f"edge_{uuid.uuid4().hex[:8]}"
        edge = GraphEdge(
            edge_id=edge_id,
            from_node=from_node,
            to_node=to_node,
            condition=condition,
        )
        self.edges.append(edge)
        return edge_id

    def expand_runtime(
        self,
        trigger_node_id: str,
        expansion_rules: list[ExpansionRule],
    ) -> list[str]:
        """
        Expand graph at runtime based on rules.

        This is called when a node completes and has expansion rules.
        """
        added_nodes = []
        trigger_node = self.nodes.get(trigger_node_id)

        if not trigger_node:
            return added_nodes

        for rule in expansion_rules:
            if self._evaluate_condition(rule.condition, trigger_node):
                for node_label, node_config in rule.expand_with:
                    new_node_id = self.add_node(
                        node_type=NodeType.ACTION,
                        label=node_label,
                        depends_on=[trigger_node_id],
                        **node_config,
                    )
                    added_nodes.append(new_node_id)

                    self.add_edge(trigger_node_id, new_node_id)

        return added_nodes

    def get_ready_nodes(self) -> list[GraphNode]:
        """Get all nodes that are ready to execute."""
        ready = []

        for node in self.nodes.values():
            if node.status != NodeStatus.PENDING:
                continue

            deps_satisfied = all(
                self.nodes[dep_id].status == NodeStatus.COMPLETED
                for dep_id in node.depends_on
            )

            if deps_satisfied:
                ready.append(node)

        return ready

    def mark_node_started(self, node_id: str) -> None:
        """Mark a node as started."""
        if node_id in self.nodes:
            self.nodes[node_id].status = NodeStatus.RUNNING
            self.nodes[node_id].started_at = datetime.utcnow()

    def mark_node_completed(self, node_id: str, result: Any = None) -> None:
        """Mark a node as completed."""
        if node_id in self.nodes:
            self.nodes[node_id].status = NodeStatus.COMPLETED
            self.nodes[node_id].completed_at = datetime.utcnow()
            self.nodes[node_id].result = result

    def mark_node_failed(self, node_id: str, error: str) -> None:
        """Mark a node as failed."""
        if node_id in self.nodes:
            self.nodes[node_id].status = NodeStatus.FAILED
            self.nodes[node_id].completed_at = datetime.utcnow()
            self.nodes[node_id].error = error

    def _evaluate_condition(self, condition: str, node: GraphNode) -> bool:
        """Evaluate an expansion condition."""
        condition_map = {
            "on_success": node.status == NodeStatus.COMPLETED and node.error is None,
            "on_failure": node.status == NodeStatus.FAILED,
            "validation_failed": (
                node.label == "validation"
                and node.result is not None
                and not node.result.get("passed", False)
            ),
        }
        return condition_map.get(condition, False)

    def get_execution_trace(self) -> list[dict]:
        """Get execution trace for debugging."""
        return [
            {
                "node_id": n.node_id,
                "type": n.node_type.value,
                "label": n.label,
                "status": n.status.value,
                "started_at": n.started_at.isoformat() if n.started_at else None,
                "completed_at": n.completed_at.isoformat() if n.completed_at else None,
                "duration_ms": (
                    (n.completed_at - n.started_at).total_seconds() * 1000
                    if n.completed_at and n.started_at else 0
                ),
                "error": n.error,
            }
            for n in sorted(
                self.nodes.values(),
                key=lambda x: x.started_at or datetime.min,
            )
        ]