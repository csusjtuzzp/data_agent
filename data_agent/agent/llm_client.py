# Copyright (c) Data Agent Team. All rights reserved.
"""LLM client for task planning and goal generation."""

import json
import re
from abc import ABC, abstractmethod
from typing import Any


# =============================================================================
# System Prompts
# =============================================================================

SYSTEM_PROMPT = """你是一个任务规划专家。请分析用户的自然语言指令，将其分解为可执行的子任务。

可用代理:
- document_parser: 文档解析代理，支持parse、extract等动作
- quality_validator: 质量验证代理，支持validate、check等动作
- structure_processor: 结构化处理代理，支持transform、format、calculate等动作

请返回结构化的执行计划JSON。"""

GOAL_PLANNING_PROMPT = """你是一个目标规划专家。请根据用户指令、当前状态和根因分析，生成要实现的目标列表。

## 用户指令
{instruction}

## 当前状态
- 页数: {page_count}
- 块数: {total_blocks}
- 状态: {status}
- 已完成目标: {completed_goals}
- 失败目标: {failed_goals}
- 最后验证: {last_validation}

## 待解决的根因
{root_causes}

## 源文件路径
{source_file}

## 可用技能列表（含参数说明）
{skill_descriptions}

## 输出要求
生成1-3个高层次目标，实现这些目标将解决问题。
每个目标应包含:
- goal_id: 唯一标识符（英文，如 parse_document, fix_tables）
- description: 目标描述（中文）
- priority: 优先级 (0=最高, 3=最低)
- success_criteria: 成功标准列表（中文）
- skill_name: 要调用的技能名称
- params: 技能参数字典（如 {{"path": "/path/to/file.pdf"}}）

## 输出格式
{{
  "goals": [
    {{"goal_id": "...", "description": "...", "priority": 1, "skill_name": "mineru_parse_skill", "params": {{"path": "/path/to/file.pdf"}}, "success_criteria": ["..."]}}
  ],
  "reasoning": "选择这些目标的推理说明"
}}"""


# =============================================================================
# LLM Client Abstract Interface
# =============================================================================

class BaseLLMClient(ABC):
    """Abstract LLM client for task planning."""

    @abstractmethod
    async def plan_tasks(self, instruction: str, input_data: Any) -> dict:
        """Use LLM to plan task decomposition."""
        pass

    @abstractmethod
    async def extract_structured_data(self, text: str, schema: dict) -> dict:
        """Extract structured data from text using LLM."""
        pass


# =============================================================================
# OpenAI LLM Client Implementation
# =============================================================================

class OpenAILLMClient(BaseLLMClient):
    """OpenAI-powered LLM client for intelligent task planning."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        api_base: str = "https://api.openai.com/v1",
    ):
        self.api_key = api_key
        self.model = model
        self.api_base = api_base

    async def plan(self, prompt: str) -> dict:
        """Use OpenAI to intelligently decompose tasks."""
        import httpx

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "response_format": {"type": "json_object"},
                },
                timeout=30.0,
            )
            response.raise_for_status()
            result = response.json()
            content = result["choices"][0]["message"]["content"]
            return json.loads(content)

    async def plan_goals(
        self,
        instruction: str,
        state: dict,
        root_causes: list[str],
        skill_descriptions: str = "",
    ) -> dict:
        """Use OpenAI to generate goal plans with dedicated goal planning prompt."""
        import httpx

        # 格式化 root_causes
        root_causes_str = "\n".join([f"- {cause}" for cause in root_causes]) if root_causes else "- 无"

        # 填充 prompt 模板
        prompt = GOAL_PLANNING_PROMPT.format(
            instruction=instruction,
            page_count=state.get("page_count", 0),
            total_blocks=state.get("total_blocks", 0),
            status=state.get("status", "unknown"),
            completed_goals=state.get("completed_goals", []),
            failed_goals=state.get("failed_goals", []),
            last_validation=state.get("last_validation", "N/A"),
            source_file=state.get("source_file", "N/A"),
            skill_descriptions=skill_descriptions or "（无可用技能信息）",
            root_causes=root_causes_str,
        )

        print(prompt)

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "user", "content": prompt},
                    ],
                    "response_format": {"type": "json_object"},
                },
                timeout=30.0,
            )
            response.raise_for_status()
            result = response.json()
            content = result["choices"][0]["message"]["content"]
            return json.loads(content)

    async def plan_tasks(self, instruction: str, input_data: Any) -> dict:
        """Use OpenAI to intelligently decompose tasks."""
        import httpx

        prompt = self._build_planning_prompt(instruction, input_data)
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "response_format": {"type": "json_object"},
                },
                timeout=30.0,
            )
            response.raise_for_status()
            result = response.json()
            content = result["choices"][0]["message"]["content"]
            return json.loads(content)

    async def extract_structured_data(self, text: str, schema: dict) -> dict:
        """Extract structured data using regex patterns (no LLM call needed)."""
        result = {}
        text_lower = text.lower()

        for field_name, field_type in schema.items():
            field_lower = field_name.lower()
            if field_type == "string":
                patterns = [
                    rf"{re.escape(field_name)}[:：]\s*(.+?)(?:\n|$)",
                    rf"\"{re.escape(field_name)}\"\s*:\s*\"(.+?)\"",
                    rf"\"{re.escape(field_lower)}\"\s*:\s*\"(.+?)\"",
                ]
                matched = False
                for pattern in patterns:
                    match = re.search(pattern, text, re.IGNORECASE)
                    if match:
                        result[field_name] = match.group(1).strip()
                        matched = True
                        break
                if not matched:
                    result[field_name] = ""
            elif field_type == "number":
                match = re.search(rf"{re.escape(field_name)}[:：]\s*([0-9.]+)", text, re.IGNORECASE)
                result[field_name] = float(match.group(1)) if match else 0
            elif field_type == "boolean":
                match = re.search(rf"{re.escape(field_name)}[:：]\s*(true|false|是|否|1|0)", text, re.IGNORECASE)
                if match:
                    val = match.group(1).lower()
                    result[field_name] = val in ("true", "是", "1")
                else:
                    result[field_name] = False
            elif field_type == "array":
                match = re.search(rf"{re.escape(field_name)}[:：]\s*\[(.+?)\]", text, re.IGNORECASE)
                if match:
                    items = [item.strip().strip("\"'") for item in match.group(1).split(",")]
                    result[field_name] = items
                else:
                    result[field_name] = []
        return result

    def _build_planning_prompt(self, instruction: str, input_data: Any) -> str:
        """Build prompt for task planning."""
        input_str = json.dumps(input_data, ensure_ascii=False, indent=2)
        return f"""请分析以下指令并规划任务执行计划。

指令: {instruction}
输入数据: {input_str}

请以JSON格式返回任务规划，包含:
- intents: 识别的意图列表
- steps: 执行步骤数组，每步包含:
  - step_id: 步骤ID
  - agent_name: 代理名称(document_parser/quality_validator/structure_processor)
  - action: 具体动作
  - input_mapping: 输入映射
  - conditions: 执行条件(可选)

示例输出:
{{
  "intents": ["parse", "validate"],
  "steps": [
    {{"step_id": "step_0", "agent_name": "document_parser", "action": "parse", "input_mapping": {{"data": {{"path": "/test.pdf"}}}}}},
    {{"step_id": "step_1", "agent_name": "quality_validator", "action": "validate", "input_mapping": {{"source_step": "step_0"}}}}
  ],
  "strategy": "pipeline"
}}
"""