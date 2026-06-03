# Copyright (c) Data Agent Team. All rights reserved.
"""Task API integration test for data-agent.

Tests the full task flow with the instruction:
"自动生成质量检测结果，空白页需要重新解析，最终生成文档元素树"

Usage:
    # Direct Python execution (from data-agent root):
    PYTHONPATH=src python tests/test_task_api.py

    # Or via pytest:
    pytest tests/test_task_api.py -v -s
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

# Demo files available for testing
DEMO_DIR = Path("/home/transwarp/Desktop/project/MinerU/demo")

DEMO_FILES = {
    "pdf": list(DEMO_DIR.glob("pdfs/*.pdf")),
    "docx": list(DEMO_DIR.glob("office_docs/*.docx")),
    "pptx": list(DEMO_DIR.glob("office_docs/*.pptx")),
    "xlsx": list(DEMO_DIR.glob("office_docs/*.xlsx")),
}


def find_test_files() -> dict:
    """Find available test files in demo directory."""
    return {
        "pdf": [str(p) for p in DEMO_DIR.glob("pdfs/*.pdf") if not p.name.startswith(".")],
        "docx": [str(p) for p in DEMO_DIR.glob("office_docs/*.docx") if not p.name.startswith(".")],
        "pptx": [str(p) for p in DEMO_DIR.glob("office_docs/*.pptx") if not p.name.startswith(".")],
        "xlsx": [str(p) for p in DEMO_DIR.glob("office_docs/*.xlsx") if not p.name.startswith(".")],
    }


# Unified instruction for all task types
TASK_INSTRUCTION = "自动生成质量检测结果，空白页需要重新解析，最终生成文档元素树"


async def submit_task_via_http(
    file_path: str,
    instruction: str = TASK_INSTRUCTION,
    api_base: str = "http://localhost:8888",
) -> dict:
    """Submit a task via HTTP POST to the running data-agent server.

    Args:
        file_path: Path to the document file
        instruction: Natural language instruction
        api_base: Base URL of the data-agent API server

    Returns:
        Task submission response with task_id
    """
    import aiohttp

    with open(file_path, "rb") as f:
        file_content = f.read()

    filename = os.path.basename(file_path)

    form_data = aiohttp.FormData()
    form_data.add_field("instruction", instruction)
    form_data.add_field("file", file_content, filename=filename, content_type="application/octet-stream")

    async with aiohttp.ClientSession() as session:
        resp = await session.post(
            f"{api_base}/tasks",
            data=form_data,
        )
        return await resp.json()


async def submit_task_direct(
    file_path: str,
    instruction: str = TASK_INSTRUCTION,
) -> dict:
    """Submit a task by directly invoking MainAgent (no server needed).

    Args:
        file_path: Path to the document file
        instruction: Natural language instruction

    Returns:
        Task result with middle_json, document_tree, etc.
    """
    from data_agent.agent.main_agent import MainAgent
    from data_agent.agent.base import AgentContext, AgentResponse
    from data_agent.agent.resource_monitor import ResourceMonitor
    from data_agent.agent.sub_agents import DocumentParser
    from data_agent.config import load_config
    from data_agent.integration import BackendSelector, MinerUClient
    from data_agent.skills import SkillRegistry, ParseSkill, FilterSkill, SkillConfig

    config = load_config()
    agent_config = config["agent"]
    mineru_config = config["mineru"]
    llm_config = config["llm"]

    # Initialize LLM client for planner
    from data_agent.agent.llm_client import OpenAILLMClient
    from data_agent.agent.planner import LLMTaskPlanner

    llm_client = OpenAILLMClient(
        api_key=llm_config.api_key,
        model=llm_config.model,
        api_base=llm_config.api_base or "https://api.openai.com/v1",
    )
    llm_planner = LLMTaskPlanner(llm_client=llm_client, enable_llm=True)

    # Initialize core components
    backend_selector = BackendSelector()
    mineru_client = MinerUClient(api_url=mineru_config.api_url)
    resource_monitor = ResourceMonitor()

    # Initialize skills
    skill_registry = SkillRegistry()
    skill_registry.register(ParseSkill(SkillConfig(name="parse_skill")))
    skill_registry.register(FilterSkill(SkillConfig(name="filter_skill")))

    # Initialize sub-agents
    sub_agents = {
        "document_parser": DocumentParser(
            skill_registry=skill_registry,
            mineru_client=mineru_client,
            backend_selector=backend_selector,
        ),
    }

    # Initialize main agent
    main_agent = MainAgent(
        sub_agents=sub_agents,
        skill_registry=skill_registry,
        resource_monitor=resource_monitor,
        llm_planner=llm_planner,
        enable_autonomous=True,
        autonomous_max_iterations=2,
        logs_dir="/tmp/data_agent_test_logs",
    )

    context = AgentContext(
        task_id="test_task",
        original_input={
            "instruction": instruction,
            "data": [{"path": file_path}],
        },
    )

    response: AgentResponse = await main_agent.execute(context)

    return {
        "success": response.success,
        "status": response.status.value,
        "error": response.error,
        "output": response.output,
    }


async def run_test_for_file(
    file_path: str,
    file_type: str,
    use_http: bool = False,
    api_base: str = "http://localhost:8888",
) -> dict:
    """Run a test for a single file."""
    filename = os.path.basename(file_path)
    print(f"\n{'='*60}")
    print(f"[TEST] File: {filename} (type: {file_type})")
    print(f"[TEST] Instruction: {TASK_INSTRUCTION}")
    print(f"{'='*60}")

    try:
        if use_http or True:
            print(f"[TEST] Submitting via HTTP to {api_base}...")
            result = await submit_task_via_http(file_path, api_base=api_base)
            task_id = result.get("task_id")
            print(f"[TEST] Task submitted: {task_id}")
            print(f"[TEST] Check status at: {result.get('status_url')}")

            # Poll for result
            import aiohttp
            import time
            async with aiohttp.ClientSession() as session:
                for attempt in range(60):
                    await asyncio.sleep(3)
                    async with session.get(f"{api_base}/tasks/{task_id}") as resp:
                        status_data = await resp.json()
                        status = status_data.get("status")
                        progress = status_data.get("progress", 0)
                        print(f"[TEST] Status: {status} (progress: {progress:.1%})")
                        if status == "completed":
                            async with session.get(f"{api_base}/tasks/{task_id}/result") as result_resp:
                                task_result = await result_resp.json()
                                print(f"[TEST] Task completed!")
                                return {"success": True, "result": task_result}
                        elif status == "failed":
                            error = status_data.get("error", "Unknown error")
                            print(f"[TEST] Task failed: {error}")
                            return {"success": False, "error": error}
                print(f"[TEST] Timeout waiting for result")
                return {"success": False, "error": "Timeout"}
        else:
            print(f"[TEST] Running direct agent execution...")
            result = await submit_task_direct(file_path)

            # Handle None output gracefully
            output = result.get("output")
            if output is None:
                print(f"[TEST] Warning: response.output is None (success={result.get('success')}, status={result.get('status')})")
                print(f"[TEST] Error: {result.get('error', 'unknown')}")
                output = {}

            middle_json = output.get("middle_json", {}) if isinstance(output, dict) else {}

            print(f"[TEST] Success: {result.get('success')}")
            print(f"[TEST] Status: {result.get('status')}")
            if result.get("error"):
                print(f"[TEST] Error: {result.get('error')}")

            # Check for expected outputs in output (document_tree may be at top level)
            has_doc_tree = "document_tree" in output or "document_tree" in middle_json
            has_txt_tree = "txt_tree" in output or "txt_tree" in middle_json
            has_stats = "doc_tree_stats" in output or "doc_tree_stats" in middle_json

            print(f"[TEST] Has document_tree: {has_doc_tree}")
            print(f"[TEST] Has txt_tree: {has_txt_tree}")
            print(f"[TEST] Has doc_tree_stats: {has_stats}")

            if has_txt_tree:
                print(f"[TEST] txt_tree preview:\n{middle_json.get('txt_tree', '' or output.get('txt_tree', ''))[:500]}")

            if has_stats:
                stats = middle_json.get("doc_tree_stats") or output.get("doc_tree_stats", {})
                print(f"[TEST] Stats: page_count={stats.get('page_count')}, "
                      f"doc_node_count={stats.get('doc_node_count')}, "
                      f"table_group_count={stats.get('table_group_count')}")

            return result

    except Exception as e:
        import traceback
        print(f"[TEST] Exception: {e}")
        print(traceback.format_exc())
        return {"success": False, "error": str(e)}


async def main():
    """Main test runner."""
    print("="*70)
    print("Data Agent Task API Test")
    print(f"Instruction: {TASK_INSTRUCTION}")
    print("="*70)

    test_files = find_test_files()

    print("\n[SETUP] Available test files:")
    for ftype, files in test_files.items():
        print(f"  {ftype}: {len(files)} files")
        for f in files[:3]:  # Show first 3
            print(f"    - {os.path.basename(f)}")

    # Ask which file type to test
    # Default to PDF for testing
    file_types_to_test = ["pdf"]
    if len(sys.argv) > 1:
        file_types_to_test = [t.strip() for t in sys.argv[1].split(",")]

    # Check if server is running for HTTP mode
    use_http = False
    api_base = os.getenv("DATA_AGENT_API_BASE", "http://localhost:8888")

    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{api_base}/health", timeout=2) as resp:
                if resp.status == 200:
                    use_http = True
                    print(f"\n[SETUP] HTTP mode: server detected at {api_base}")
                else:
                    print(f"\n[SETUP] Direct mode: server returned {resp.status}")
    except Exception:
        print(f"\n[SETUP] Direct mode: server not available at {api_base}")

    if not use_http:
        print(f"[SETUP] Will use direct MainAgent invocation")

    print(f"[SETUP] File types to test: {file_types_to_test}")
    print(f"[SETUP] Test mode: {'HTTP' if use_http else 'DIRECT'}")

    results = {}

    for file_type in file_types_to_test:
        files = test_files.get(file_type, [])
        if not files:
            print(f"\n[SKIP] No {file_type} files found")
            continue

        # Test with all files of each type
        for test_file in files:
            file_name = os.path.basename(test_file)
            print(f"\n[TEST] Testing {file_type} file: {file_name}")
            result = await run_test_for_file(
                test_file,
                file_type,
                use_http=use_http,
                api_base=api_base,
            )
            results[f"{file_type}/{file_name}"] = result

    # Summary
    print("\n" + "="*70)
    print("TEST SUMMARY")
    print("="*70)
    for file_type, result in results.items():
        status = "PASS" if result.get("success") else "FAIL"
        error = result.get("error", "")
        print(f"  {file_type}: {status}" + (f" ({error})" if error else ""))

    return results


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        print("\nUsage:")
        print("  python test_task_api.py              # Test PDF files (default)")
        print("  python test_task_api.py pdf,docx     # Test specific file types")
        print("  python test_task_api.py --http       # Use HTTP mode (requires server)")
        sys.exit(0)

    asyncio.run(main())