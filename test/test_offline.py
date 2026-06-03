# Copyright (c) Data Agent Team. All rights reserved.
"""Offline test script for document-parser and backend-selector."""

import asyncio
import json
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from data_agent.agent.sub_agents.document_parser import DocumentParser
from data_agent.agent.base import AgentContext, AgentStatus
from data_agent.integration.backend_selector import BackendSelector
from data_agent.integration.mineru_client import MinerUClient
from data_agent.config import load_config


# Test files from demo directory
DEMO_FILES = {
    "pdf": [
        "/home/transwarp/Desktop/project/MinerU/demo/pdfs/demo1.pdf",
        "/home/transwarp/Desktop/project/MinerU/demo/pdfs/demo2.pdf",
        "/home/transwarp/Desktop/project/MinerU/demo/pdfs/demo3.pdf",
        "/home/transwarp/Desktop/project/MinerU/demo/pdfs/small_ocr.pdf",
    ],
    "docx": [
        "/home/transwarp/Desktop/project/MinerU/demo/office_docs/docx_01.docx",
    ],
    "pptx": [
        "/home/transwarp/Desktop/project/MinerU/demo/office_docs/pptx_01.pptx",
    ],
    "xlsx": [
        "/home/transwarp/Desktop/project/MinerU/demo/office_docs/xlsx_01.xlsx",
    ],
}


def print_header(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print('=' * 60)


def print_result(name: str, result, elapsed: float = None) -> None:
    print(f"\n--- {name} ---")
    if result.success:
        print(f"  Status: SUCCESS")
        if elapsed:
            print(f"  Time: {elapsed:.2f}s")
        output = result.output or {}
        print(f"  Backend: {output.get('backend')}")
        print(f"  Doc Type: {output.get('doc_type')}")
        metadata = output.get("metadata", {})
        print(f"  File: {metadata.get('file_path')}")
        page_count = metadata.get("page_count", "N/A")
        print(f"  Page Count: {page_count}")
        middle_json = output.get("middle_json", {})
        if middle_json:
            print(f"  Middle JSON Keys: {list(middle_json.keys())}")
            if "pdf_info" in middle_json:
                print(f"  PDF Info Pages: {len(middle_json['pdf_info'])}")
    else:
        print(f"  Status: FAILED")
        print(f"  Error: {result.error or 'Unknown error'}")
        if result.context and result.context.metadata.get("llm_reasoning"):
            print(f"  LLM Reasoning: {result.context.metadata.get('llm_reasoning')}")


async def test_backend_selector():
    """Test backend selection for different file types."""
    print_header("Backend Selector Tests")

    selector = BackendSelector()
    results = []

    for doc_type, files in DEMO_FILES.items():
        for file_path in files:
            if Path(file_path).exists():
                print(f"\nTesting: {file_path}")
                result = await selector.select_backend(
                    file_path=file_path,
                    doc_type=doc_type,
                )
                print(f"  Selected Backend: {result.backend}")
                print(f"  Confidence: {result.confidence}")
                print(f"  Reasoning: {result.reasoning}")
                results.append({
                    "file": file_path,
                    "doc_type": doc_type,
                    "backend": result.backend,
                    "confidence": result.confidence,
                })

    return results


async def test_document_parser(files: list[str], output_dir: str = "/tmp/test_results"):
    """Test document parser with real MinerU integration."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Load config
    config = load_config()
    mineru_config = config["mineru"]

    # Create real dependencies
    selector = BackendSelector()
    mineru_client = MinerUClient(
        api_url=mineru_config.api_url,
        timeout=mineru_config.timeout,
    )

    parser = DocumentParser(
        backend_selector=selector,
        mineru_client=mineru_client,
    )

    results = []
    for file_path in files:
        if not Path(file_path).exists():
            print(f"  [SKIP] File not found: {file_path}")
            continue

        print(f"\nTesting: {file_path}")

        import time
        start_time = time.time()

        context = AgentContext(
            task_id="test_task",
            original_input={"path": file_path, "filename": Path(file_path).name},
            metadata={},
        )

        result = await parser.execute(context)
        elapsed = time.time() - start_time

        print_result(Path(file_path).name, result, elapsed)

        # Save middle_json to file
        if result.success and result.output:
            middle_json = result.output.get("middle_json", {})
            output_path = Path(output_dir) / f"{Path(file_path).stem}_middle.json"
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(middle_json, f, ensure_ascii=False, indent=2)
            print(f"  Middle JSON saved to: {output_path}")

            # Also save metadata
            meta_path = Path(output_dir) / f"{Path(file_path).stem}_meta.json"
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump({
                    "backend": result.output.get("backend"),
                    "doc_type": result.output.get("doc_type"),
                    "metadata": result.output.get("metadata"),
                    "elapsed_seconds": elapsed,
                }, f, ensure_ascii=False, indent=2)
            print(f"  Metadata saved to: {meta_path}")

            # Save parse log for this file
            log_path = Path(output_dir) / f"{Path(file_path).stem}_parse.log"
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(f"=== Document Parser Log ===\n")
                f.write(f"File: {file_path}\n")
                f.write(f"Backend: {result.output.get('backend')}\n")
                f.write(f"Doc Type: {result.output.get('doc_type')}\n")
                f.write(f"Page Count: {result.output.get('metadata', {}).get('page_count', 'N/A')}\n")
                f.write(f"Parse Time: {elapsed:.2f}s\n")
                f.write(f"Status: {'SUCCESS' if result.success else 'FAILED'}\n")
                if result.error:
                    f.write(f"Error: {result.error}\n")
                f.write(f"\n=== Middle JSON Info ===\n")
                f.write(f"Keys: {list(middle_json.keys())}\n")
                f.write(f"PDF Info Pages: {len(middle_json.get('pdf_info', []))}\n")
            print(f"  Parse log saved to: {log_path}")

        results.append({
            "file": file_path,
            "success": result.success,
            "error": result.error,
            "elapsed": elapsed,
        })

    # Cleanup
    await mineru_client.close()

    return results


async def main():
    print("=" * 60)
    print("  Data Agent - Real Integration Test Script")
    print("  Testing Document Parser with MinerU Backend")
    print("=" * 60)

    # Set default MinerU API URL if not set
    if not os.getenv("MINERU_API_URL"):
        os.environ["MINERU_API_URL"] = ""

    # Load config
    config = load_config()
    print(f"\nMinerU API URL: {config['mineru'].api_url}")

    # Test 1: Backend Selector
    print_header("Test 1: Backend Selector")
    backend_results = await test_backend_selector()
    print(f"\nBackend Selection Summary:")
    for r in backend_results:
        print(f"  {r['doc_type']:6} -> {r['backend']:20} ({r['file'].split('/')[-1]})")

    # Test 2: Document Parser with all file types
    print_header("Test 2: Document Parser (Real MinerU Integration)")
    all_files = []
    for files in DEMO_FILES.values():
        all_files.extend(files)

    parser_results = await test_document_parser(all_files)

    # Summary
    print_header("Test Summary")
    total = len(parser_results)
    passed = sum(1 for r in parser_results if r["success"])
    print(f"  Total: {total}")
    print(f"  Passed: {passed}")
    print(f"  Failed: {total - passed}")

    if parser_results:
        total_time = sum(r.get("elapsed", 0) for r in parser_results)
        print(f"  Total Time: {total_time:.2f}s")
        print(f"  Avg Time: {total_time/total:.2f}s per file")

    # Backend selector summary
    print(f"\nBackend Selector tested {len(backend_results)} files")

    # List output files
    output_dir = Path("/tmp/test_results")
    if output_dir.exists():
        print(f"\nOutput files in {output_dir}:")
        for f in sorted(output_dir.glob("*_middle.json")):
            print(f"  - {f.name}")

    return parser_results


if __name__ == "__main__":
    results = asyncio.run(main())