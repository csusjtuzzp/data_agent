#!/usr/bin/env python
"""Standalone test script for DocumentTreeBuilder - avoids __init__.py dependency chain."""

import sys
import os

# Add paths directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'data-agent/src'))

# Import directly from the document_tree module path to avoid sub_agents/__init__.py
from data_agent.agent.sub_agents.document_tree.builder import DocumentTreeBuilder
from data_agent.agent.sub_agents.document_tree.doc_tree import DocNodeType


# Sample middle_json for testing
test_middle_json = {
    "pdf_info": [
        {
            "page_idx": 0,
            "page_size": [595.0, 842.0],
            "preproc_blocks": [
                {
                    "index": 0,
                    "type": "title",
                    "bbox": [50.0, 700.0, 545.0, 750.0],
                    "confidence": 0.99,
                    "lines": [
                        {
                            "bbox": [50.0, 700.0, 545.0, 750.0],
                            "spans": [
                                {"text": "3.2 财务分析", "font_size": 24, "font_name": "bold"}
                            ]
                        }
                    ]
                },
                {
                    "index": 1,
                    "type": "text",
                    "bbox": [50.0, 600.0, 545.0, 680.0],
                    "confidence": 0.95,
                    "lines": [
                        {
                            "bbox": [50.0, 600.0, 545.0, 620.0],
                            "spans": [
                                {"text": "本报告分析了公司上半年的财务状况...", "font_size": 12}
                            ]
                        }
                    ]
                },
                {
                    "index": 2,
                    "type": "table",
                    "bbox": [50.0, 400.0, 545.0, 580.0],
                    "confidence": 0.90,
                    "lines": []
                }
            ]
        },
        {
            "page_idx": 1,
            "page_size": [595.0, 842.0],
            "preproc_blocks": [
                {
                    "index": 3,
                    "type": "table",
                    "bbox": [50.0, 50.0, 545.0, 200.0],
                    "confidence": 0.90,
                    "lines": []
                },
                {
                    "index": 4,
                    "type": "text",
                    "bbox": [50.0, 250.0, 545.0, 350.0],
                    "confidence": 0.95,
                    "lines": [
                        {
                            "bbox": [50.0, 250.0, 545.0, 350.0],
                            "spans": [
                                {"text": "综上所述，公司整体运营良好...", "font_size": 12}
                            ]
                        }
                    ]
                },
                {
                    "index": 5,
                    "type": "figure",
                    "bbox": [50.0, 400.0, 300.0, 600.0],
                    "confidence": 0.85,
                    "lines": []
                }
            ]
        }
    ],
    "_backend": "test",
    "_version_name": "1.0.0"
}


async def test():
    builder = DocumentTreeBuilder()

    class MockContext:
        def __init__(self):
            self.task_id = "test_task"
            self.original_input = {"middle_json": test_middle_json}

    context = MockContext()
    result = await builder.execute(context)

    print("=" * 60)
    print("TEST RESULT")
    print("=" * 60)

    if result.success:
        print(f"Status: SUCCESS")
        print(f"\nStats:")
        for k, v in result.output["stats"].items():
            print(f"  {k}: {v}")

        print(f"\n--- TXT Tree ---")
        print(result.output["txt_tree"])

        print(f"\n--- JSON Tree (truncated) ---")
        doc_tree = result.output["document_tree"]
        print(f"Root: {doc_tree['root']}")
        print(f"Node count: {len(doc_tree['nodes'])}")

        # Count by type
        type_counts = {}
        for nid, node in doc_tree["nodes"].items():
            nt = node["node_type"]
            type_counts[nt] = type_counts.get(nt, 0) + 1
        print(f"Node types: {type_counts}")
    else:
        print(f"Status: FAILED")
        print(f"Error: {result.error}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(test())