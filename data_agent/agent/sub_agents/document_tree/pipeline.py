# Copyright (c) Data Agent Team. All rights reserved.
"""Six-stage pipeline for building Document Tree from middle_json."""

import re
import statistics
from typing import Optional

from data_agent.agent.sub_agents.document_tree.layout_graph import (
    LayoutGraph,
    LayoutNode,
    LayoutEdge,
    EdgeType,
)
from data_agent.agent.sub_agents.document_tree.doc_tree import (
    DocumentTree,
    DocNode,
    DocNodeType,
    RelationType,
)


class BlockNormalizeStage:
    """Stage 1: Normalize blocks from all pages into a unified Layout Graph."""

    LAYOUT_TYPES = {"page_number", "header", "footer", "page_footnote", "separator", "aside_text"}

    def process(self, middle_json: dict) -> LayoutGraph:
        """Convert middle_json blocks to LayoutNodes with normalized properties."""
        graph = LayoutGraph()

        for page_info in middle_json.get("pdf_info", []):
            page_idx = page_info.get("page_idx", 0)
            page_size = page_info.get("page_size", (0, 0))
            page_width, page_height = page_size

            # Handle both preproc_blocks and para_blocks formats
            # Use para_blocks if preproc_blocks is empty (office formats store data in para_blocks)
            preproc = page_info.get("preproc_blocks")
            blocks = preproc if preproc else page_info.get("para_blocks", [])

            for block in blocks:
                if block.get("type") in self.LAYOUT_TYPES:
                    continue
                node = self._block_to_node(block, page_idx, page_width, page_height)
                if node:
                    graph.add_node(node)

        return graph

    def _block_to_node(self, block: dict, page_idx: int, page_width: float, page_height: float) -> Optional[LayoutNode]:
        """Convert a single block to LayoutNode with normalized coordinates."""
        block_type = block.get("type", "unknown")
        if block_type in self.LAYOUT_TYPES:
            return None

        bbox = block.get("bbox", [0, 0, 0, 0])

        # If bbox is empty/invalid, try to compute from lines
        if not bbox or len(bbox) != 4 or bbox == [0, 0, 0, 0]:
            bbox = self._compute_bbox_from_lines(block)
            if not bbox:
                bbox = [0, 0, 0, 0]

        x0, y0, x1, y1 = bbox

        style = self._extract_style(block)
        text = self._extract_text(block)

        node = LayoutNode(
            block_id=str(block.get("index", "")),
            type=block_type,
            text=text,
            bbox=(x0, y0, x1, y1),
            page=page_idx,
            index=block.get("index", 0),
            confidence=block.get("confidence", 1.0),
            style=style,
        )

        return node

    def _compute_bbox_from_lines(self, block: dict) -> Optional[list]:
        """Compute bbox from lines/spans if bbox is missing."""
        lines = block.get("lines", [])
        if not lines:
            return None

        min_x, min_y = float('inf'), float('inf')
        max_x, max_y = float('-inf'), float('-inf')

        for line in lines:
            line_bbox = line.get("bbox", [])
            if line_bbox and len(line_bbox) == 4:
                min_x = min(min_x, line_bbox[0])
                min_y = min(min_y, line_bbox[1])
                max_x = max(max_x, line_bbox[2])
                max_y = max(max_y, line_bbox[3])

            for span in line.get("spans", []):
                span_bbox = span.get("bbox", [])
                if span_bbox and len(span_bbox) == 4:
                    min_x = min(min_x, span_bbox[0])
                    min_y = min(min_y, span_bbox[1])
                    max_x = max(max_x, span_bbox[2])
                    max_y = max(max_y, span_bbox[3])

        if min_x == float('inf'):
            return None

        return [min_x, min_y, max_x, max_y]

    def _extract_style(self, block: dict) -> dict:
        """Extract font/style information from block."""
        style = {"font_size": 0, "bold": False, "italic": False}

        lines = block.get("lines", [])
        if lines:
            first_line = lines[0]
            spans = first_line.get("spans", [])
            if spans:
                span = spans[0]
                style["font_size"] = span.get("font_size", span.get("size", 0))
                font_name = span.get("font_name", "")
                style["bold"] = "bold" in font_name.lower() if font_name else False
                style["italic"] = "italic" in font_name.lower() if font_name else False

        return style

    def _extract_text(self, block: dict) -> str:
        """Extract text content from block."""
        # Check text key only if it has meaningful content (not empty/placeholder)
        text_val = block.get("text", "")
        if text_val and text_val.strip() and text_val != "EMPTY":
            return text_val

        # Also check "content" field in block
        content_val = block.get("content", "")
        if content_val and content_val.strip():
            return content_val

        text_parts = []
        # Handle lines directly in block
        for line in block.get("lines", []):
            line_text = self._extract_line_text(line)
            if line_text:
                text_parts.append(line_text)

        # Handle nested blocks (for table/chart types in office formats)
        for nested_block in block.get("blocks", []):
            nested_text = self._extract_text(nested_block)
            if nested_text:
                text_parts.append(nested_text)

        return "\n".join(text_parts)

    def _extract_line_text(self, line: dict) -> str:
        """Extract text from a line/spans structure."""
        line_text = ""
        for span in line.get("spans", []):
            # Try "text" first, then "content"
            span_text = span.get("text", "") or span.get("content", "") or ""
            # For table spans (office formats), extract text from html
            if not span_text and span.get("type") == "table" and span.get("html"):
                span_text = self._extract_html_text(span.get("html"))
            # For table spans, also check html content
            if not span_text and span.get("html"):
                span_text = self._extract_html_text(span.get("html"))
            line_text += span_text
        return line_text

    def _extract_html_text(self, html: str) -> str:
        """Extract text from HTML content (for table cells)."""
        import re
        # Remove HTML tags and get text content
        text = re.sub(r'<[^>]+>', '', html)
        text = text.strip()
        # Normalize whitespace
        text = re.sub(r'\s+', ' ', text)
        return text


class ReadingOrderStage:
    """Stage 2: Reconstruct reading order using Layout Graph + topological sort."""

    COLUMN_SPLIT_THRESHOLD = 0.5
    VERTICAL_GAP_THRESHOLD = 0.02

    def process(self, graph: LayoutGraph, page_count: int) -> LayoutGraph:
        """Reconstruct reading order across pages with multi-column support."""

        for page_idx in range(page_count):
            page_nodes = [n for n in graph.nodes.values() if n.page == page_idx]
            self._build_page_ordering(graph, page_nodes)

        self._build_cross_page_ordering(graph, page_count)
        self._topological_sort_pages(graph)

        return graph

    def _build_page_ordering(self, graph: LayoutGraph, page_nodes: list[LayoutNode]) -> None:
        """Build reading order edges within a single page."""
        if not page_nodes:
            return

        # Filter out layout types
        content_nodes = [n for n in page_nodes if n.type not in {"page_number", "header", "footer", "separator"}]
        if not content_nodes:
            return

        # Sort by vertical position (top to bottom), then horizontal (left to right)
        sorted_nodes = sorted(content_nodes, key=lambda n: (n.top, n.left))

        # Detect columns based on center position (normalized)
        left_col = []
        right_col = []

        page_width = page_nodes[0].bbox[2] if page_nodes and page_nodes[0].bbox else 1
        if page_width == 0:
            page_width = 1

        for node in sorted_nodes:
            center_x_normalized = node.center_x / page_width if page_width > 0 else 0.5
            if center_x_normalized < 0.5:
                left_col.append(node)
            else:
                right_col.append(node)

        # Build NEXT edges within each column
        for col in [left_col, right_col]:
            col_sorted = sorted(col, key=lambda n: n.top)
            for i in range(len(col_sorted) - 1):
                current, next_node = col_sorted[i], col_sorted[i + 1]
                self._add_edge_if_not_exists(graph, current.node_id, next_node.node_id, EdgeType.NEXT)

        # Build ALIGNED_WITH edges for column pairs at similar vertical positions
        if left_col and right_col:
            for left_node in left_col:
                for right_node in right_col:
                    if abs(left_node.top - right_node.top) < 0.02:
                        graph.add_edge(LayoutEdge(
                            source_id=left_node.node_id,
                            target_id=right_node.node_id,
                            edge_type=EdgeType.ALIGNED_WITH,
                            weight=0.8
                        ))

    def _build_cross_page_ordering(self, graph: LayoutGraph, page_count: int) -> None:
        """Build reading order edges between pages."""
        for page_idx in range(page_count - 1):
            current_nodes = [n for n in graph.nodes.values() if n.page == page_idx]
            next_nodes = [n for n in graph.nodes.values() if n.page == page_idx + 1]

            if not current_nodes or not next_nodes:
                continue

            first_next = self._find_first_content_node(next_nodes)
            last_current = self._find_last_content_node(current_nodes)

            if first_next and last_current:
                graph.add_edge(LayoutEdge(
                    source_id=last_current.node_id,
                    target_id=first_next.node_id,
                    edge_type=EdgeType.CROSS_PAGE,
                    weight=1.0
                ))

    def _find_first_content_node(self, nodes: list[LayoutNode]) -> Optional[LayoutNode]:
        """Find first content node on page."""
        content = [n for n in nodes if n.type not in {"page_number", "header", "footer", "separator"}]
        return min(content, key=lambda n: (n.top, n.left)) if content else None

    def _find_last_content_node(self, nodes: list[LayoutNode]) -> Optional[LayoutNode]:
        """Find last content node on page."""
        content = [n for n in nodes if n.type not in {"page_number", "header", "footer", "separator"}]
        return max(content, key=lambda n: (n.top, n.left)) if content else None

    def _topological_sort_pages(self, graph: LayoutGraph) -> None:
        """Topological sort for each page to establish final reading order."""
        pages = set(n.page for n in graph.nodes.values())
        for page_idx in pages:
            page_nodes = [n for n in graph.nodes.values() if n.page == page_idx]
            self._sort_page_nodes(graph, page_nodes)

    def _sort_page_nodes(self, graph: LayoutGraph, page_nodes: list[LayoutNode]) -> None:
        """Sort nodes within a page using topological sort."""
        node_ids = set(n.node_id for n in page_nodes)
        sorted_order = []
        remaining = set(node_ids)

        while remaining:
            for node_id in list(remaining):
                predecessors = graph.get_incoming(node_id, EdgeType.NEXT)
                if all(p.node_id in sorted_order for p in predecessors):
                    sorted_order.append(node_id)
                    remaining.remove(node_id)
                    break
            else:
                # Remaining nodes without NEXT edges - add them
                sorted_order.extend(list(remaining))
                break

        for order, node_id in enumerate(sorted_order):
            if node_id in graph.nodes:
                graph.nodes[node_id].reading_order = order

    def _add_edge_if_not_exists(self, graph: LayoutGraph, source_id: str, target_id: str, edge_type: EdgeType) -> None:
        """Add edge only if it doesn't already exist."""
        for edge in graph.edges:
            if edge.source_id == source_id and edge.target_id == target_id and edge.edge_type == edge_type:
                return
        graph.add_edge(LayoutEdge(source_id=source_id, target_id=target_id, edge_type=edge_type))


class SectionHierarchyStage:
    """Stage 3: Detect section hierarchy based on font size, bold, and numbering patterns."""

    H1_THRESHOLD = 1.5
    H2_THRESHOLD = 1.3
    H3_THRESHOLD = 1.15

    # Strict numbering patterns for Chinese headings only
    NUMBERING_PATTERNS = [
        r"^第[一二三四五六七八九十]+章",
        r"^第[一二三四五六七八九十]+节",
        r"^第[0-9]+章",
        r"^第[0-9]+节",
    ]

    # Strict heading patterns only (complete phrases)
    HEADING_KEYWORDS = [
        "第一章", "第二章", "第三章", "第四章", "第五章",
        "第一节", "第二节", "第三节",
    ]

    def process(self, graph: LayoutGraph) -> LayoutGraph:
        """Detect section hierarchy and mark heading nodes."""

        base_font_size = self._calculate_base_font_size(graph)

        for node in graph.nodes.values():
            if node.type not in {"text", "title", "paragraph_title"}:
                continue

            font_size = node.style.get("font_size", base_font_size)
            is_bold = node.style.get("bold", False)
            text = node.text

            heading_level = self._detect_heading_level(text, font_size, is_bold, base_font_size, node.height, node.type)

            if heading_level > 0:
                node.is_heading = True
                node.heading_level = heading_level
                node.style["heading_level"] = heading_level

        return graph

    def _calculate_base_font_size(self, graph: LayoutGraph) -> float:
        """Calculate base font size from body text nodes."""
        font_sizes = []
        for node in graph.nodes.values():
            if node.type == "text" and node.style.get("font_size", 0) > 0:
                font_sizes.append(node.style["font_size"])

        # If no font_size available, estimate from block height
        if not font_sizes:
            heights = [n.height for n in graph.nodes.values() if n.type == "text" and n.height > 0]
            if heights:
                estimated_font_sizes = [h / 1.3 for h in heights]
                return statistics.median(estimated_font_sizes) if estimated_font_sizes else 12.0

        return statistics.median(font_sizes) if font_sizes else 12.0

    def _detect_heading_level(self, text: str, font_size: float, is_bold: bool, base: float, block_height: float = 0, block_type: str = "") -> int:
        """Detect heading level from font size and content."""
        if not text or not text.strip():
            return 0

        stripped = text.strip()

        # Only consider as heading if text is short
        if len(stripped) > 80:
            return 0

        # Check numbering patterns first
        for pattern in self.NUMBERING_PATTERNS:
            if re.match(pattern, stripped):
                return self._level_from_pattern(stripped)

        # Check heading keywords (strict patterns)
        for keyword in self.HEADING_KEYWORDS:
            if stripped.startswith(keyword):
                return self._level_from_pattern(stripped)

        # For title-type blocks in office documents (sheet names like Sheet1, Sheet2)
        # always treat as heading level 1
        if block_type == "title" and stripped:
            return 1

        # Check font size ratio if available
        if font_size > 0 and base > 0:
            ratio = font_size / base
            if ratio >= self.H1_THRESHOLD:
                return 1
            elif ratio >= self.H2_THRESHOLD:
                return 2
            elif ratio >= self.H3_THRESHOLD:
                return 3

        return 0

    def _level_from_pattern(self, text: str) -> int:
        """Determine heading level from numbering pattern."""
        # Chinese chapter patterns
        if "第" in text and "章" in text:
            if re.match(r"^第[一二三四五六七八九十]+章", text):
                return 1
            if "节" in text:
                return 2
            return 1
        if "第" in text and "节" in text:
            return 2

        # Numeric patterns like 1.2.3
        numeric_match = re.match(r"^(\d+)\.(\d+)\.(.*)", text)
        if numeric_match:
            dots = len(numeric_match.group(0).split(".")) - 1
            return min(dots, 3)

        # Simple 1.2 pattern
        if re.match(r"^\d+\.\d+$", text):
            return 2

        # Single level numeric like 1. 2. 3.
        if re.match(r"^[0-9]+[.、\.．]", text):
            return 2

        # Alphabet patterns A. B. C.
        if re.match(r"^[A-Z][.、\.．]", text):
            return 2

        # Default to section level
        return 2

        if ratio >= self.H1_THRESHOLD or (is_bold and ratio >= self.H2_THRESHOLD):
            return 1
        elif ratio >= self.H2_THRESHOLD or (is_bold and ratio >= self.H3_THRESHOLD):
            return 2
        elif ratio >= self.H3_THRESHOLD:
            return 3

        return 0

    def _level_from_pattern(self, text: str) -> int:
        """Determine heading level from numbering pattern."""
        if "第" in text and "章" in text:
            return 1
        elif "第" in text and "节" in text:
            return 2
        elif re.match(r"^\d+\.\d+\.", text):
            return min(len(text.split(".")) - 1, 3)
        return 2


class CrossPageMergeStage:
    """Stage 4: Merge cross-page content (tables, figures, paragraphs)."""

    def process(self, graph: LayoutGraph, middle_json: dict) -> LayoutGraph:
        """Merge cross-page tables, figures, and paragraphs."""

        graph = self._merge_cross_page_tables(graph, middle_json)
        graph = self._merge_cross_page_paragraphs(graph)

        return graph

    def _merge_cross_page_tables(self, graph: LayoutGraph, middle_json: dict) -> LayoutGraph:
        """Merge tables that span multiple pages."""
        page_info_list = middle_json.get("pdf_info", [])
        merged_node_ids = set()

        # Find last table on page N and first table on page N+1
        for page_idx in range(len(page_info_list) - 1):
            current_page = page_info_list[page_idx]
            next_page = page_info_list[page_idx + 1]

            current_table = self._find_last_table(current_page)
            next_table = self._find_first_table(next_page)

            if current_table and next_table:
                current_node = self._find_node_by_block_id(graph, current_table.get("index"))
                next_node = self._find_node_by_block_id(graph, next_table.get("index"))

                if current_node and next_node:
                    # Create CONTINUES edge
                    graph.add_edge(LayoutEdge(
                        source_id=current_node.node_id,
                        target_id=next_node.node_id,
                        edge_type=EdgeType.CONTINUES,
                        metadata={"merge_type": "cross_page_table"}
                    ))
                    merged_node_ids.add(next_node.node_id)

        # Mark merged nodes
        for node_id in merged_node_ids:
            if node_id in graph.nodes:
                graph.nodes[node_id].style["cross_page_merged"] = True

        return graph

    def _merge_cross_page_paragraphs(self, graph: LayoutGraph) -> LayoutGraph:
        """Merge paragraphs that span multiple pages."""
        if not graph.nodes:
            return graph

        page_count = max(n.page for n in graph.nodes.values()) + 1

        for page_idx in range(page_count - 1):
            current_nodes = [n for n in graph.nodes.values() if n.page == page_idx]
            next_nodes = [n for n in graph.nodes.values() if n.page == page_idx + 1]

            if not current_nodes or not next_nodes:
                continue

            last_para = self._find_last_paragraph(current_nodes)
            first_para = self._find_first_paragraph(next_nodes)

            if last_para and first_para:
                graph.add_edge(LayoutEdge(
                    source_id=last_para.node_id,
                    target_id=first_para.node_id,
                    edge_type=EdgeType.CONTINUES,
                    metadata={"merge_type": "cross_page_paragraph"}
                ))

        return graph

    def _find_last_table(self, page_info: dict) -> Optional[dict]:
        tables = [b for b in page_info.get("preproc_blocks", []) if b.get("type") == "table"]
        return tables[-1] if tables else None

    def _find_first_table(self, page_info: dict) -> Optional[dict]:
        tables = [b for b in page_info.get("preproc_blocks", []) if b.get("type") == "table"]
        return tables[0] if tables else None

    def _find_last_paragraph(self, nodes: list[LayoutNode]) -> Optional[LayoutNode]:
        paras = [n for n in nodes if n.type == "text"]
        return max(paras, key=lambda n: n.top) if paras else None

    def _find_first_paragraph(self, nodes: list[LayoutNode]) -> Optional[LayoutNode]:
        paras = [n for n in nodes if n.type == "text"]
        return min(paras, key=lambda n: n.top) if paras else None

    def _find_node_by_block_id(self, graph: LayoutGraph, block_id: int) -> Optional[LayoutNode]:
        for node in graph.nodes.values():
            if node.index == block_id:
                return node
        return None


class SemanticGroupingStage:
    """Stage 5: Group related elements (heading + body + figures) into semantic units."""

    def process(self, graph: LayoutGraph) -> LayoutGraph:
        """Group nodes into semantic sections."""

        self._group_heading_content(graph)
        self._group_caption_content(graph)
        self._group_lists(graph)

        return graph

    def _group_heading_content(self, graph: LayoutGraph) -> None:
        """Group heading with its following content."""
        heading_nodes = [n for n in graph.nodes.values() if n.is_heading]

        for heading in sorted(heading_nodes, key=lambda n: (n.page, n.top)):
            following = self._get_following_content(graph, heading)

            for content_node in following:
                if content_node.is_heading and content_node.heading_level <= heading.heading_level:
                    break

                graph.add_edge(LayoutEdge(
                    source_id=heading.node_id,
                    target_id=content_node.node_id,
                    edge_type=EdgeType.CONTAINS,
                    metadata={"group_type": "section"}
                ))

    def _get_following_content(self, graph: LayoutGraph, heading: LayoutNode) -> list[LayoutNode]:
        """Get content that follows a heading until next heading."""
        result = []
        current = heading
        visited = set()

        while current and current.node_id not in visited:
            visited.add(current.node_id)
            next_nodes = graph.get_outgoing(current.node_id, EdgeType.NEXT)

            if not next_nodes:
                break

            next_node = next_nodes[0]

            if next_node.is_heading:
                break

            if next_node.type not in {"page_number", "header", "footer", "separator"}:
                result.append(next_node)

            current = next_node

        return result

    def _group_caption_content(self, graph: LayoutGraph) -> None:
        """Group captions with their referenced content."""
        for node in graph.nodes.values():
            if "caption" in node.type:
                nearby = self._find_nearby_content(graph, node)
                for target in nearby:
                    graph.add_edge(LayoutEdge(
                        source_id=node.node_id,
                        target_id=target.node_id,
                        edge_type=EdgeType.CAPTION_OF,
                        metadata={"direction": "caption_of_target"}
                    ))

    def _find_nearby_content(self, graph: LayoutGraph, caption: LayoutNode) -> list[LayoutNode]:
        """Find content near a caption that it likely describes."""
        nearby = []

        for node in graph.nodes.values():
            if node.page != caption.page:
                continue
            if node.node_id == caption.node_id:
                continue

            if node.type in {"table", "figure", "image"}:
                if 0 <= caption.top - node.bottom < 0.1:
                    nearby.append(node)

        return nearby

    def _group_lists(self, graph: LayoutGraph) -> None:
        """Identify and group list structures."""
        list_nodes = [n for n in graph.nodes.values() if n.type == "list"]

        current_list = None
        for node in sorted(list_nodes, key=lambda n: (n.page, n.top)):
            if not current_list:
                current_list = node
                continue

            if abs(current_list.top - node.top) < 0.02 and abs(current_list.left - node.left) < 0.02:
                graph.add_edge(LayoutEdge(
                    source_id=current_list.node_id,
                    target_id=node.node_id,
                    edge_type=EdgeType.NEXT,
                    metadata={"group_type": "list_item"}
                ))
            else:
                current_list = node


class TreeConstructionStage:
    """Stage 6: Build final Document Tree from Layout Graph."""

    def process(self, graph: LayoutGraph) -> DocumentTree:
        """Construct Document Tree from Layout Graph."""
        tree = DocumentTree()

        # Create root document node
        root = DocNode(
            node_type=DocNodeType.DOCUMENT,
            title="Document",
            level=0
        )
        tree.add_node(root)
        tree.root = root.node_id

        # Group nodes by page
        pages = sorted(set(n.page for n in graph.nodes.values()))

        # Build chapters for each page
        for page_idx in pages:
            page_nodes = [n for n in graph.nodes.values() if n.page == page_idx]
            chapter = self._build_page_chapter(graph, page_nodes, page_idx, tree)
            tree.add_node(chapter)
            root.add_child(chapter.node_id)

        # Build sections within chapters
        self._build_sections(graph, tree)

        # Group cross-page tables into TABLE_GROUP nodes
        self._group_cross_page_tables(graph, tree)

        return tree

    def _build_page_chapter(self, graph: LayoutGraph, page_nodes: list[LayoutNode], page_idx: int, tree: DocumentTree) -> DocNode:
        """Build a chapter node for a page."""
        chapter = DocNode(
            node_type=DocNodeType.CHAPTER,
            title=f"Page {page_idx + 1}",
            level=1,
            page_range=(page_idx, page_idx),
            metadata={"page_idx": page_idx}
        )
        tree.add_node(chapter)

        # Build all content sections for this page
        content_nodes = [n for n in page_nodes if n.type not in {"page_number", "header", "footer", "separator"}]

        if content_nodes:
            # Create a default section for this page if no headings found
            default_section = DocNode(
                node_type=DocNodeType.SECTION,
                title=f"Page {page_idx + 1} Content",
                level=2,
                page_range=(page_idx, page_idx)
            )
            tree.add_node(default_section)
            chapter.add_child(default_section.node_id)

            # Add all content nodes to the default section
            for node in sorted(content_nodes, key=lambda n: n.top):
                doc_node = self._create_doc_node_from_layout_node(node, tree)
                if doc_node:
                    tree.add_node(doc_node)
                    default_section.add_child(doc_node.node_id)

        return chapter

    def _create_doc_node_from_layout_node(self, node: LayoutNode, tree: DocumentTree) -> Optional[DocNode]:
        """Create a DocNode from a LayoutNode."""
        if node.is_heading:
            level = min(node.heading_level, 3)
            return DocNode(
                node_type=DocNodeType.SECTION if level <= 2 else DocNodeType.SUBSECTION,
                title=node.text[:100] if node.text else f"Section {node.index}",
                level=level + 1,
                page_range=(node.page, node.page),
                metadata={"heading_level": level}
            )
        else:
            node_type_map = {
                "table": DocNodeType.TABLE,
                "figure": DocNodeType.FIGURE,
                "image": DocNodeType.FIGURE,
                "chart": DocNodeType.FIGURE,
                "equation": DocNodeType.EQUATION,
                "list": DocNodeType.LIST,
                "title": DocNodeType.SECTION,
                "text": DocNodeType.PARAGRAPH,
                "paragraph": DocNodeType.PARAGRAPH,
            }
            doc_node_type = node_type_map.get(node.type, DocNodeType.PARAGRAPH)

            return DocNode(
                node_type=doc_node_type,
                content=node.text if node.text else "",
                title=node.text[:50] if node.text and doc_node_type == DocNodeType.SECTION else "",
                level=3,
                page_range=(node.page, node.page),
                source_nodes=[node.node_id],
                metadata={"types": [node.type]}
            )

    def _build_page_section(self, graph: LayoutGraph, page_nodes: list[LayoutNode], parent_id: str, tree: DocumentTree) -> Optional[DocNode]:
        """Build section node for content on a page."""
        section = None
        created_section_this_iteration = False

        for node in sorted(page_nodes, key=lambda n: n.top):
            if node.type in {"page_number", "header", "footer", "separator"}:
                continue

            if node.is_heading:
                level = min(node.heading_level, 3)
                section = DocNode(
                    node_type=DocNodeType.SECTION if level <= 2 else DocNodeType.SUBSECTION,
                    title=node.text[:100],
                    level=level + 1,
                    page_range=(node.page, node.page),
                    metadata={"heading_level": level}
                )
                tree.add_node(section)
                created_section_this_iteration = True
            else:
                # Map block type to DocNodeType
                node_type_map = {
                    "table": DocNodeType.TABLE,
                    "figure": DocNodeType.FIGURE,
                    "image": DocNodeType.FIGURE,
                    "equation": DocNodeType.EQUATION,
                    "list": DocNodeType.LIST,
                    "title": DocNodeType.PARAGRAPH,  # Titles become section titles, not paragraphs
                }
                doc_node_type = node_type_map.get(node.type, DocNodeType.PARAGRAPH)

                para = DocNode(
                    node_type=doc_node_type,
                    content=node.text,
                    level=3 if section else 2,
                    page_range=(node.page, node.page),
                    source_nodes=[node.node_id],
                    metadata={"types": [node.type]}
                )
                tree.add_node(para)

                if section and not created_section_this_iteration:
                    section.add_child(para.node_id)
                elif not created_section_this_iteration:
                    # Create default section - use first content block as title if available
                    section_title = node.text[:50] if node.text else "Content"
                    if len(section_title) > 30:
                        section_title = section_title[:30] + "..."
                    section = DocNode(
                        node_type=DocNodeType.SECTION,
                        title=section_title,
                        level=2,
                        page_range=(node.page, node.page)
                    )
                    tree.add_node(section)
                    section.add_child(para.node_id)
                    created_section_this_iteration = True

                # Reset flag after handling non-heading
                created_section_this_iteration = False

        return section

    def _build_sections(self, graph: LayoutGraph, tree: DocumentTree) -> None:
        """Build sections within chapters based on heading hierarchy.

        Note: This is kept for additional heading-based sections beyond what
        _build_page_chapter already creates. It only adds SECTION nodes for
        heading nodes that weren't already processed.
        """
        # Track which pages already have content sections from _build_page_chapter
        pages_with_content = set()
        for node in tree.nodes.values():
            if node.node_type == DocNodeType.CHAPTER:
                pages_with_content.add(node.page_range[0])

        for node in graph.nodes.values():
            if not node.is_heading:
                continue

            # Skip if this page was already processed by _build_page_chapter
            if node.page in pages_with_content:
                continue

            level = min(node.heading_level, 3)
            doc_node = DocNode(
                node_type=DocNodeType.SECTION if level <= 2 else DocNodeType.SUBSECTION,
                title=node.text[:100] if node.text else f"Section {node.index}",
                level=level + 1,
                page_range=(node.page, node.page),
                metadata={"heading_level": level}
            )

            # Find following content using CONTAINS edges
            following = graph.get_outgoing(node.node_id, EdgeType.CONTAINS)
            for child_node in following:
                if not child_node.is_heading:
                    doc_node.add_child(child_node.node_id)

            if tree.root and tree.nodes:
                parent_chapter = self._find_parent_chapter(tree, node.page)
                if parent_chapter:
                    parent_chapter.add_child(doc_node.node_id)
                    tree.add_node(doc_node)

    def _find_parent_chapter(self, tree: DocumentTree, page: int) -> Optional[DocNode]:
        """Find chapter containing the given page."""
        for node in tree.nodes.values():
            if node.node_type == DocNodeType.CHAPTER and node.page_range[0] <= page <= node.page_range[1]:
                return node
        return None

    def _group_cross_page_tables(self, graph: LayoutGraph, tree: DocumentTree) -> None:
        """Group cross-page tables into TABLE_GROUP nodes."""
        continue_edges = [e for e in graph.edges if e.edge_type == EdgeType.CONTINUES]

        merged_groups = {}
        for edge in continue_edges:
            if edge.metadata.get("merge_type") == "cross_page_table":
                source = graph.nodes.get(edge.source_id)
                target = graph.nodes.get(edge.target_id)
                if source and target:
                    group_id = source.node_id
                    if group_id not in merged_groups:
                        merged_groups[group_id] = {
                            "title": source.text or "Cross-page Table",
                            "pages": set([source.page]),
                            "source_nodes": [source.node_id]
                        }
                    merged_groups[group_id]["pages"].add(target.page)
                    merged_groups[group_id]["source_nodes"].append(target.node_id)

        # Create TABLE_GROUP nodes
        for group_id, group_data in merged_groups.items():
            table_group = DocNode(
                node_type=DocNodeType.TABLE_GROUP,
                title=group_data["title"],
                level=3,
                page_range=(min(group_data["pages"]), max(group_data["pages"])),
                source_nodes=group_data["source_nodes"],
                metadata={"source_type": "cross_page_table"}
            )

            # Find parent section and add
            parent_section = self._find_parent_chapter(tree, min(group_data["pages"]))
            if parent_section:
                parent_section.add_child(table_group.node_id)
                tree.add_node(table_group)