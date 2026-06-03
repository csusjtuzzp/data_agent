# Copyright (c) Data Agent Team. All rights reserved.
"""Document Tree data structures for semantic document representation."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import uuid


class DocNodeType(Enum):
    """Node types in the semantic Document Tree."""
    DOCUMENT = "document"
    CHAPTER = "chapter"
    SECTION = "section"
    SUBSECTION = "subsection"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    TABLE_GROUP = "table_group"        # Cross-page merged tables
    FIGURE = "figure"
    EQUATION = "equation"
    LIST = "list"
    FOOTNOTE = "footnote"
    APPENDIX = "appendix"


class RelationType(Enum):
    """Relation types between DocNodes."""
    CONTAINS = "contains"
    CAPTION_OF = "caption_of"
    REFERS_TO = "refers_to"
    CONTINUES = "continues"
    NEXT = "next"


@dataclass
class DocNode:
    """Node in the semantic Document Tree."""
    node_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    node_type: DocNodeType = DocNodeType.DOCUMENT
    title: str = ""
    content: str = ""
    children: list[str] = field(default_factory=list)  # Child node IDs
    metadata: dict = field(default_factory=dict)
    relations: list[tuple[str, RelationType]] = field(default_factory=list)  # (target_id, relation)

    # Source reference
    source_nodes: list[str] = field(default_factory=list)  # LayoutNode IDs

    # Properties
    level: int = 0                      # Hierarchical level (1=chapter, 2=section, etc.)
    page_range: tuple[int, int] = (0, 0)  # (start_page, end_page)

    def add_child(self, child_id: str) -> None:
        if child_id not in self.children:
            self.children.append(child_id)

    def add_relation(self, target_id: str, relation: RelationType) -> None:
        self.relations.append((target_id, relation))

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "node_id": self.node_id,
            "node_type": self.node_type.value,
            "title": self.title,
            "content": self.content,
            "children": self.children,
            "metadata": self.metadata,
            "source_nodes": self.source_nodes,
            "level": self.level,
            "page_range": list(self.page_range),
        }


@dataclass
class DocumentTree:
    """The final semantic Document Tree."""
    root: Optional[str] = None
    nodes: dict[str, DocNode] = field(default_factory=dict)

    # Indexes for fast lookup
    _by_type: dict[DocNodeType, list[str]] = field(default_factory=dict)
    _by_page: dict[int, list[str]] = field(default_factory=dict)

    def add_node(self, node: DocNode) -> None:
        self.nodes[node.node_id] = node
        self._update_indexes(node)

    def _update_indexes(self, node: DocNode) -> None:
        """Update type and page indexes."""
        if node.node_type not in self._by_type:
            self._by_type[node.node_type] = []
        if node.node_id not in self._by_type[node.node_type]:
            self._by_type[node.node_type].append(node.node_id)

        for page in range(node.page_range[0], node.page_range[1] + 1):
            if page not in self._by_page:
                self._by_page[page] = []
            if node.node_id not in self._by_page[page]:
                self._by_page[page].append(node.node_id)

    def get_nodes_by_type(self, node_type: DocNodeType) -> list[DocNode]:
        return [self.nodes[nid] for nid in self._by_type.get(node_type, [])]

    def get_nodes_by_page(self, page: int) -> list[DocNode]:
        return [self.nodes[nid] for nid in self._by_page.get(page, [])]

    def to_dict(self) -> dict:
        """Serialize to JSON relationship diagram."""
        return {
            "root": self.root,
            "nodes": {nid: node.to_dict() for nid, node in self.nodes.items()}
        }

    def to_txt_tree(self, max_content_length: int = 150) -> str:
        """Generate ASCII tree visualization with full content."""
        if not self.root or self.root not in self.nodes:
            return "(empty tree)"

        lines = []
        self._build_tree_lines(self.root, lines, "", True, max_content_length)
        return "\n".join(lines)

    def _build_tree_lines(self, node_id: str, lines: list, prefix: str, is_last: bool, max_content_length: int = 150) -> None:
        """Recursively build tree lines with full content display."""
        node = self.nodes.get(node_id)
        if not node:
            return

        # Build label based on node type and content
        if node.title:
            node_label = f"{node.node_type.value.title()}: {node.title}"
        elif node.content:
            content_preview = node.content[:max_content_length] + ("..." if len(node.content) > max_content_length else "")
            node_label = f"{node.node_type.value.title()}: {content_preview}"
        else:
            # Show metadata for empty nodes
            types = node.metadata.get("types", [])
            if types:
                node_label = f"{node.node_type.value.title()}: [{', '.join(types)}]"
            else:
                node_label = f"{node.node_type.value.title()}"

        # Show page range
        if node.page_range[0] != node.page_range[1]:
            node_label += f" (p.{node.page_range[0]}-{node.page_range[1]})"
        elif node.page_range[0] > 0:
            node_label += f" (p.{node.page_range[0]})"

        connector = "└── " if is_last else "├── "
        lines.append(prefix + connector + node_label)

        # Children
        child_prefix = prefix + ("    " if is_last else "│   ")
        for i, child_id in enumerate(node.children):
            is_last_child = i == len(node.children) - 1
            self._build_tree_lines(child_id, lines, child_prefix, is_last_child, max_content_length)