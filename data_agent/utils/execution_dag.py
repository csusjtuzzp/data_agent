# Copyright (c) Data Agent Team. All rights reserved.
"""Execution DAG - Agent 执行图可视化"""

from enum import Enum
from typing import Optional
from dataclasses import dataclass, field


class NodeStatus(Enum):
    """节点状态"""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass
class DAGNode:
    """DAG 节点"""
    name: str
    node_type: str  # "agent", "skill"
    status: NodeStatus = NodeStatus.PENDING
    parent: Optional["DAGNode"] = None
    children: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class ExecutionDAG:
    """Agent 执行图管理器

    结构:
        MainAgent
         ├── ParseAgent
         │     ├── OCRSkill
         │     └── MinerUSkill
         ├── ReflectionAgent
         ├── TableAgent
         └── GraphAgent
    """

    def __init__(self, task_id: str):
        self.task_id = task_id
        self._nodes = {}
        self._root = None
        self._current_node = None
        self._build_static_structure()

    def _build_static_structure(self):
        """构建静态结构"""
        # MainAgent
        main_agent = DAGNode(name="MainAgent", node_type="agent")
        self._nodes["MainAgent"] = main_agent
        self._root = main_agent

        # ParseAgent
        parse_agent = DAGNode(name="ParseAgent", node_type="agent", parent=main_agent)
        self._nodes["ParseAgent"] = parse_agent
        main_agent.children.append(parse_agent)

        # OCRSkill
        ocr_skill = DAGNode(name="OCRSkill", node_type="skill", parent=parse_agent)
        self._nodes["OCRSkill"] = ocr_skill
        parse_agent.children.append(ocr_skill)

        # MinerUSkill
        mineru_skill = DAGNode(name="MinerUSkill", node_type="skill", parent=parse_agent)
        self._nodes["MinerUSkill"] = mineru_skill
        parse_agent.children.append(mineru_skill)

        # ReflectionAgent
        reflection_agent = DAGNode(name="ReflectionAgent", node_type="agent", parent=main_agent)
        self._nodes["ReflectionAgent"] = reflection_agent
        main_agent.children.append(reflection_agent)

        # TableAgent
        table_agent = DAGNode(name="TableAgent", node_type="agent", parent=main_agent)
        self._nodes["TableAgent"] = table_agent
        main_agent.children.append(table_agent)

        # GraphAgent
        graph_agent = DAGNode(name="GraphAgent", node_type="agent", parent=main_agent)
        self._nodes["GraphAgent"] = graph_agent
        main_agent.children.append(graph_agent)

    def set_node_status(self, node_name: str, status: NodeStatus):
        """设置节点状态"""
        if node_name in self._nodes:
            self._nodes[node_name].status = status

    def get_node(self, node_name: str) -> Optional[DAGNode]:
        """获取节点"""
        return self._nodes.get(node_name)

    def get_current_node(self) -> Optional[DAGNode]:
        """获取当前执行节点"""
        return self._current_node

    def to_string(self) -> str:
        """转换为字符串表示"""
        lines = []
        self._render_node(self._root, lines, "", True)
        return "\n".join(lines)

    def _render_node(self, node: DAGNode, lines: list, prefix: str, is_last: bool):
        """递归渲染节点"""
        connector = "└── " if is_last else "├── "
        status_icon = self._get_status_icon(node.status)
        lines.append(f"{prefix}{connector}{status_icon}{node.name}")

        new_prefix = prefix + ("    " if is_last else "│   ")
        for i, child in enumerate(node.children):
            is_last_child = i == len(node.children) - 1
            self._render_node(child, lines, new_prefix, is_last_child)

    def _get_status_icon(self, status: NodeStatus) -> str:
        """获取状态图标"""
        icons = {
            NodeStatus.PENDING: "○ ",
            NodeStatus.RUNNING: "◉ ",
            NodeStatus.SUCCESS: "✓ ",
            NodeStatus.FAILED: "✗ ",
        }
        return icons.get(status, "○ ")

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "task_id": self.task_id,
            "nodes": {
                name: {
                    "name": node.name,
                    "type": node.node_type,
                    "status": node.status.value,
                    "metadata": node.metadata,
                }
                for name, node in self._nodes.items()
            },
            "structure": self.to_string(),
        }

    def reset(self):
        """重置所有节点状态"""
        for node in self._nodes.values():
            node.status = NodeStatus.PENDING