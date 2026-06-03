# Copyright (c) Data Agent Team. All rights reserved.
"""Layout Graph data structures for document structure analysis."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import uuid


class EdgeType(Enum):
    """Edge types in Layout Graph representing spatial and semantic relationships."""
    NEXT = "next"                      # Sequential reading order
    CONTAINS = "contains"              # Parent-child containment
    CAPTION_OF = "caption_of"          # Caption → referent relationship
    REFERS_TO = "refers_to"            # Cross-reference
    CONTINUES = "continues"            # Split content continuation
    ALIGNED_WITH = "aligned_with"      # Multi-column alignment
    SAME_SECTION = "same_section"      # Same section membership
    CROSS_PAGE = "cross_page"          # Cross-page content
    HEADER_OF = "header_of"           # Page header association
    FOOTNOTE_OF = "footnote_of"       # Footnote association


@dataclass
class LayoutNode:
    """Node in the Layout Graph representing a layout element."""
    node_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    block_id: str = ""                # Original block reference
    type: str = ""                    # Block type (text|title|image|table|...)
    text: str = ""
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)  # (left, top, right, bottom)
    page: int = 0
    index: int = 0                     # Original block index
    confidence: float = 1.0
    style: dict = field(default_factory=dict)  # font_size, bold, italic, etc.

    # Computed properties
    left: float = 0.0
    right: float = 0.0
    top: float = 0.0
    bottom: float = 0.0
    width: float = 0.0
    height: float = 0.0
    center_x: float = 0.0
    center_y: float = 0.0

    # Semantic markers (set during processing)
    is_heading: bool = False
    heading_level: int = 0
    reading_order: int = 0

    def __post_init__(self):
        if self.bbox and len(self.bbox) == 4:
            self.left, self.top, self.right, self.bottom = self.bbox
            self.width = self.right - self.left
            self.height = self.bottom - self.top
            self.center_x = (self.left + self.right) / 2
            self.center_y = (self.top + self.bottom) / 2

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "node_id": self.node_id,
            "block_id": self.block_id,
            "type": self.type,
            "text": self.text[:200] if self.text else "",
            "bbox": self.bbox,
            "page": self.page,
            "index": self.index,
            "confidence": self.confidence,
            "style": self.style,
            "is_heading": self.is_heading,
            "heading_level": self.heading_level,
            "reading_order": self.reading_order,
        }


@dataclass
class LayoutEdge:
    """Edge in the Layout Graph."""
    source_id: str
    target_id: str
    edge_type: EdgeType
    weight: float = 1.0
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "edge_type": self.edge_type.value,
            "weight": self.weight,
            "metadata": self.metadata,
        }


@dataclass
class LayoutGraph:
    """Layout Graph containing nodes and edges."""
    nodes: dict[str, LayoutNode] = field(default_factory=dict)
    edges: list[LayoutEdge] = field(default_factory=list)

    def add_node(self, node: LayoutNode) -> None:
        self.nodes[node.node_id] = node

    def add_edge(self, edge: LayoutEdge) -> None:
        self.edges.append(edge)

    def get_outgoing(self, node_id: str, edge_type: EdgeType = None) -> list[LayoutNode]:
        """Get nodes connected from given node."""
        result = []
        for edge in self.edges:
            if edge.source_id == node_id:
                if edge_type is None or edge.edge_type == edge_type:
                    target = self.nodes.get(edge.target_id)
                    if target:
                        result.append(target)
        return result

    def get_incoming(self, node_id: str, edge_type: EdgeType = None) -> list[LayoutNode]:
        """Get nodes connecting to given node."""
        result = []
        for edge in self.edges:
            if edge.target_id == node_id:
                if edge_type is None or edge.edge_type == edge_type:
                    source = self.nodes.get(edge.source_id)
                    if source:
                        result.append(source)
        return result

    def get_node_count(self) -> int:
        return len(self.nodes)

    def get_edge_count(self) -> int:
        return len(self.edges)

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "nodes": {nid: node.to_dict() for nid, node in self.nodes.items()},
            "edges": [edge.to_dict() for edge in self.edges],
        }