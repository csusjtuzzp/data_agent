# Copyright (c) Data Agent Team. All rights reserved.
"""Base sub-agent class."""

from typing import TYPE_CHECKING, Any, Optional

from data_agent.agent.base import AgentContext, AgentResponse, BaseAgent

if TYPE_CHECKING:
    from data_agent.skills.registry import SkillRegistry


class BaseSubAgent(BaseAgent):
    """Base class for all sub-agents."""

    def __init__(
        self,
        name: str,
        skill_registry: Optional["SkillRegistry"] = None,
        config: dict = None,
    ):
        super().__init__(name, config)
        self.skill_registry = skill_registry
        self._skills: dict[str, Any] = {}

    def register_skill(self, skill_name: str, skill_instance: Any) -> None:
        """Register a skill for this agent to use."""
        self._skills[skill_name] = skill_instance

    def get_skill(self, skill_name: str) -> Any:
        """Get a registered skill by name."""
        return self._skills.get(skill_name)

    async def pre_execute(self, context: AgentContext) -> None:
        await super().pre_execute(context)
        if not self.skill_registry:
            return

        required_skills = self.config.get("skill_requirements", [])
        for skill_name in required_skills:
            skill = self.skill_registry.get_skill(skill_name)
            if skill:
                self.register_skill(skill_name, skill)
