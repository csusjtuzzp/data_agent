# Copyright (c) Data Agent Team. All rights reserved.
"""Base skill class and skill configuration."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class SkillConfig:
    """Configuration for a skill."""

    name: str
    version: str = "1.0.0"
    parameters: dict = None

    def __post_init__(self):
        if self.parameters is None:
            self.parameters = {}


class BaseSkill(ABC):
    """Base class for all skills."""

    def __init__(self, config: SkillConfig):
        self.config = config
        self.name = config.name
        self.version = config.version
        self._enabled = True

    @abstractmethod
    async def execute(self, data: Any, **kwargs) -> Any:
        """Execute the skill."""
        pass

    @abstractmethod
    def validate_input(self, data: Any) -> bool:
        """Validate input data."""
        pass

    def enable(self) -> None:
        """Enable the skill."""
        self._enabled = True

    def disable(self) -> None:
        """Disable the skill."""
        self._enabled = False

    @property
    def is_enabled(self) -> bool:
        """Check if skill is enabled."""
        return self._enabled
