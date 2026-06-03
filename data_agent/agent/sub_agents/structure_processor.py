# Copyright (c) Data Agent Team. All rights reserved.
"""Structure Processor sub-agent."""

from typing import Any, Optional

from data_agent.agent.base import AgentContext, AgentResponse, AgentStatus
from data_agent.agent.sub_agents.base_sub_agent import BaseSubAgent


class StructureProcessor(BaseSubAgent):
    """Sub-agent for processing and transforming middle_json structures."""

    def __init__(self, skill_registry: Optional[Any] = None):
        super().__init__("StructureProcessor", skill_registry)
        self.format_skill = None

    async def execute(self, context: AgentContext) -> AgentResponse:
        await self.pre_execute(context)

        input_data = context.original_input
        middle_json = input_data.get("middle_json")
        transformation = input_data.get("transformation", "default")

        try:
            result = middle_json

            if self.format_skill:
                result = await self.format_skill.execute(
                    data=result,
                    transformation=transformation,
                )
            else:
                result = self._default_transform(middle_json)

            return AgentResponse(
                success=True,
                status=AgentStatus.COMPLETED,
                output={
                    "processed_json": result,
                    "transformation_applied": transformation,
                },
                context=context,
            )

        except Exception as e:
            return AgentResponse(
                success=False,
                status=AgentStatus.FAILED,
                error=str(e),
                context=context,
            )

    def _default_transform(self, middle_json: dict) -> dict:
        """Apply default structural transformations."""
        return {
            "pdf_info": middle_json.get("pdf_info", []),
            "_backend": middle_json.get("_backend"),
            "_version_name": middle_json.get("_version_name"),
            "_processed": True,
        }
