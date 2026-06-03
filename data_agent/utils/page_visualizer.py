# Copyright (c) Data Agent Team. All rights reserved.
"""Page Visualizer - 页面解析可视化"""

from enum import Enum
from typing import Optional
from dataclasses import dataclass, field


class ViewMode(Enum):
    """视图模式"""
    RAW_OCR = "raw OCR"
    MIDDLE_JSON = "middle_json"
    FINAL_TREE = "final tree"


@dataclass
class Block:
    """解析块"""
    block_type: str  # title/table/figure/formula
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2
    content: str
    color: str = "green"


@dataclass
class PageVisualization:
    """单页可视化数据"""
    page_num: int
    blocks: list[Block] = field(default_factory=list)
    raw_ocr: str = ""
    middle_json: dict = field(default_factory=dict)
    final_tree: dict = field(default_factory=dict)


class PageVisualizer:
    """页面解析可视化管理器

    布局:
        ┌─────────────────────────────────────────────────────┐
        │  原始PDF页              │  解析结果Overlay          │
        │  ┌─────────────────┐    │  ┌─────────────────┐     │
        │  │                 │    │  │ [绿色框] Title  │     │
        │  │                 │    │  │ [蓝色框] Table  │     │
        │  │                 │    │  │ [黄色框] Figure │     │
        │  │                 │    │  │ [紫色框] Formula │     │
        │  └─────────────────┘    │  └─────────────────┘     │
        └─────────────────────────────────────────────────────┘
    """

    # 块类型到颜色的映射
    BLOCK_COLORS = {
        "title": "green",
        "table": "blue",
        "figure": "yellow",
        "formula": "purple",
        "text": "white",
        "list": "cyan",
    }

    def __init__(self, task_id: str):
        self.task_id = task_id
        self._pages: dict[int, PageVisualization] = {}
        self._uuid_url = f"/{task_id}/"

    def add_page(self, page_num: int) -> PageVisualization:
        """添加一页"""
        page = PageVisualization(page_num=page_num)
        self._pages[page_num] = page
        return page

    def add_block(self, page_num: int, block_type: str, bbox: tuple, content: str):
        """添加块到页面"""
        if page_num not in self._pages:
            self.add_page(page_num)

        page = self._pages[page_num]
        color = self.BLOCK_COLORS.get(block_type, "green")
        block = Block(block_type=block_type, bbox=bbox, content=content, color=color)
        page.blocks.append(block)

    def set_raw_ocr(self, page_num: int, raw_ocr: str):
        """设置原始 OCR"""
        if page_num not in self._pages:
            self.add_page(page_num)
        self._pages[page_num].raw_ocr = raw_ocr

    def set_middle_json(self, page_num: int, middle_json: dict):
        """设置中间层 JSON"""
        if page_num not in self._pages:
            self.add_page(page_num)
        self._pages[page_num].middle_json = middle_json

    def set_final_tree(self, page_num: int, final_tree: dict):
        """设置最终结构"""
        if page_num not in self._pages:
            self.add_page(page_num)
        self._pages[page_num].final_tree = final_tree

    def get_page(self, page_num: int) -> Optional[PageVisualization]:
        """获取页面"""
        return self._pages.get(page_num)

    def get_all_pages(self) -> dict[int, PageVisualization]:
        """获取所有页面"""
        return self._pages

    def get_uuid_url(self) -> str:
        """获取 UUID URL"""
        return self._uuid_url

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "task_id": self.task_id,
            "uuid_url": self._uuid_url,
            "pages": {
                page_num: {
                    "page_num": page.page_num,
                    "blocks": [
                        {
                            "type": b.block_type,
                            "bbox": b.bbox,
                            "content": b.content,
                            "color": b.color,
                        }
                        for b in page.blocks
                    ],
                    "raw_ocr": page.raw_ocr,
                    "middle_json": page.middle_json,
                    "final_tree": page.final_tree,
                }
                for page_num, page in self._pages.items()
            },
        }