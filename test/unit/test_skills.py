# Copyright (c) Data Agent Team. All rights reserved.
"""Unit tests for skills."""

import pytest
from data_agent.skills.base_skill import SkillConfig
from data_agent.skills.parse_skill import ParseSkill
from data_agent.skills.format_skill import FormatSkill
from data_agent.skills.filter_skill import FilterSkill


@pytest.fixture
def sample_middle_json():
    """Create sample middle_json for testing."""
    return {
        "pdf_info": [
            {
                "page_idx": 0,
                "page_size": [612, 792],
                "preproc_blocks": [
                    {"type": "text", "text": "Hello World", "confidence": 0.9},
                    {"type": "table", "text": "", "confidence": 0.8},
                ],
            },
            {
                "page_idx": 1,
                "page_size": [612, 792],
                "preproc_blocks": [
                    {"type": "text", "text": "Page 2", "confidence": 0.95},
                ],
            },
        ],
        "_backend": "pipeline",
        "_version_name": "1.0.0",
    }


@pytest.mark.asyncio
async def test_parse_skill_clean_empty_pages(sample_middle_json):
    """Test ParseSkill removes empty pages."""
    config = SkillConfig(name="parse_skill", parameters={"clean_empty_pages": True})
    skill = ParseSkill(config)

    result = await skill.execute(sample_middle_json)

    assert len(result["pdf_info"]) == 2


@pytest.mark.asyncio
async def test_parse_skill_extract_table_metadata(sample_middle_json):
    """Test ParseSkill extracts table metadata."""
    config = SkillConfig(name="parse_skill", parameters={"extract_tables": True})
    skill = ParseSkill(config)

    result = await skill.execute(sample_middle_json)

    first_page_blocks = result["pdf_info"][0]["preproc_blocks"]
    table_block = next(b for b in first_page_blocks if b["type"] == "table")
    assert table_block.get("_has_table") is True


@pytest.mark.asyncio
async def test_format_skill_flatten(sample_middle_json):
    """Test FormatSkill flatten transformation."""
    config = SkillConfig(name="format_skill", parameters={"default": "flatten"})
    skill = FormatSkill(config)

    result = await skill.execute(sample_middle_json, transformation="flatten")

    assert "pdf_info" in result
    assert "_backend" in result
    assert "_version" in result


@pytest.mark.asyncio
async def test_format_skill_extract_text(sample_middle_json):
    """Test FormatSkill extract_text transformation."""
    config = SkillConfig(name="format_skill")
    skill = FormatSkill(config)

    result = await skill.execute(sample_middle_json, transformation="extract_text")

    assert "text" in result
    assert "Hello World" in result["text"]
    assert "Page 2" in result["text"]


@pytest.mark.asyncio
async def test_filter_skill_by_confidence(sample_middle_json):
    """Test FilterSkill filters by confidence."""
    config = SkillConfig(
        name="filter_skill", parameters={"min_confidence": 0.85}
    )
    skill = FilterSkill(config)

    result = await skill.execute(sample_middle_json)

    all_blocks = [b for p in result["pdf_info"] for b in p["preproc_blocks"]]
    assert all(b.get("confidence", 1.0) >= 0.85 for b in all_blocks)


@pytest.mark.asyncio
async def test_filter_skill_deduplicate():
    """Test FilterSkill removes duplicates."""
    middle_json_with_dups = {
        "pdf_info": [
            {
                "page_idx": 0,
                "preproc_blocks": [
                    {"type": "text", "text": "Hello"},
                    {"type": "text", "text": "Hello"},
                    {"type": "text", "text": "World"},
                ],
            }
        ],
        "_backend": "pipeline",
        "_version_name": "1.0.0",
    }

    config = SkillConfig(
        name="filter_skill", parameters={"remove_duplicates": True}
    )
    skill = FilterSkill(config)

    result = await skill.execute(middle_json_with_dups)

    blocks = result["pdf_info"][0]["preproc_blocks"]
    assert len(blocks) == 2
