# Copyright (c) Data Agent Team. All rights reserved.
"""Skill composition for chaining multiple skills."""

import asyncio
from typing import Any, Callable, Optional

from data_agent.skills.base_skill import BaseSkill, SkillConfig


class CompositeSkill(BaseSkill):
    """Composition of multiple skills."""

    def __init__(
        self, skills: list[BaseSkill], operator: str = "chain"
    ):
        super().__init__(SkillConfig(name="composite"))
        self.skills = skills
        self.operator = operator

    async def execute(self, data: Any, **kwargs) -> Any:
        """Execute skills based on operator."""
        if self.operator == "chain":
            return await self._chain_execute(data, **kwargs)
        elif self.operator == "parallel":
            return await self._parallel_execute(data, **kwargs)
        elif self.operator == "fallback":
            return await self._fallback_execute(data, **kwargs)
        return data

    async def _chain_execute(self, data: Any, **kwargs) -> Any:
        """Execute skills in sequence."""
        result = data
        for skill in self.skills:
            if skill.is_enabled:
                result = await skill.execute(result, **kwargs)
        return result

    async def _parallel_execute(self, data: Any, **kwargs) -> Any:
        """Execute skills in parallel."""
        tasks = [
            skill.execute(data, **kwargs)
            for skill in self.skills
            if skill.is_enabled
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return results

    async def _fallback_execute(self, data: Any, **kwargs) -> Any:
        """Try skills in order until one succeeds."""
        last_error = None
        for skill in self.skills:
            if not skill.is_enabled:
                continue
            try:
                return await skill.execute(data, **kwargs)
            except Exception as e:
                last_error = e
                continue
        if last_error:
            raise last_error
        return data

    def validate_input(self, data: Any) -> bool:
        """Validation delegates to first enabled skill."""
        for skill in self.skills:
            if skill.is_enabled:
                return skill.validate_input(data)
        return True
