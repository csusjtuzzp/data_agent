# Copyright (c) Data Agent Team. All rights reserved.
"""Integration tests."""

import pytest


@pytest.mark.asyncio
async def test_end_to_end_parsing():
    """Test end-to-end document parsing."""
    pytest.skip("Requires MinerU to be installed")

    from data_agent.integration.mineru_client import MinerUClient
    from pathlib import Path

    client = MinerUClient()

    test_file = Path("/tmp/test.pdf")
    if not test_file.exists():
        pytest.skip("Test file not found")

    middle_json, model_output = await client.parse(
        file_path=str(test_file),
        backend="pipeline",
    )

    assert middle_json is not None
    assert "pdf_info" in middle_json
    assert "_backend" in middle_json
