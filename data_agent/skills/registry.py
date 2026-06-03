# Copyright (c) Data Agent Team. All rights reserved.
"""Skill registry for managing available skills with action discovery."""

from dataclasses import dataclass, field
from typing import Optional

from loguru import logger

from data_agent.skills.base_skill import BaseSkill, SkillConfig


@dataclass
class SkillMetadata:
    """Metadata for skill action discovery."""
    description: str = ""
    category: str = "general"
    preconditions: list[str] = field(default_factory=list)
    postconditions: list[str] = field(default_factory=list)
    addresses_issues: list[str] = field(default_factory=list)
    default_params: dict = field(default_factory=dict)
    action_patterns: list[str] = field(default_factory=list)
    param_descriptions: dict = field(default_factory=dict)  # 参数描述，如 {"path": "文件路径", "backend": "解析后端"}


class SkillRegistry:
    """
    Registry for managing available skills with action discovery.

    Enhanced version supports:
    - Action metadata for discovery
    - Category-based queries
    - Plugin-based skill registration
    - Dynamic skill loading
    """

    def __init__(self):
        self._skills: dict[str, BaseSkill] = {}
        self._aliases: dict[str, str] = {}
        self._metadata: dict[str, SkillMetadata] = {}
        self._categories: dict[str, list[str]] = {}

    def register(
        self,
        skill: BaseSkill,
        aliases: Optional[list[str]] = None,
        metadata: Optional[SkillMetadata] = None,
    ) -> None:
        """Register a skill with optional metadata."""
        self._skills[skill.name] = skill

        if aliases:
            for alias in aliases:
                self._aliases[alias] = skill.name

        if metadata:
            self._metadata[skill.name] = metadata

            category = metadata.category
            if category not in self._categories:
                self._categories[category] = []
            if skill.name not in self._categories[category]:
                self._categories[category].append(skill.name)

        logger.debug(f"[SkillRegistry] Registered skill: {skill.name}")

    def register_with_metadata(
        self,
        skill_class: type,
        config: SkillConfig,
        **metadata_kwargs,
    ) -> None:
        """Register a skill with standard metadata."""
        skill = skill_class(config)
        metadata = SkillMetadata(**metadata_kwargs)
        self.register(skill, metadata=metadata)

    def get_skill(self, name: str) -> Optional[BaseSkill]:
        """Get a skill by name or alias."""
        skill_name = self._aliases.get(name, name)
        return self._skills.get(skill_name)

    def get_metadata(self, skill_name: str) -> Optional[SkillMetadata]:
        """Get metadata for a skill."""
        return self._metadata.get(skill_name)

    def list_skills(self, category: Optional[str] = None) -> list[str]:
        """List all registered skill names, optionally filtered by category."""
        if category:
            return self._categories.get(category, []).copy()
        return list(self._skills.keys())

    def list_categories(self) -> list[str]:
        """List all skill categories."""
        return list(self._categories.keys())

    def discover_actions(self, issue: str) -> list[str]:
        """Discover skills that can address a specific issue."""
        matching_skills = []

        for skill_name, metadata in self._metadata.items():
            if issue in metadata.addresses_issues:
                matching_skills.append(skill_name)

        return matching_skills

    def create_composite(
        self, skill_names: list[str], operator: str = "chain"
    ) -> "CompositeSkill":
        """Create a composite skill from multiple skills."""
        from data_agent.skills.composition import CompositeSkill

        skills = [self.get_skill(name) for name in skill_names]
        skills = [s for s in skills if s is not None]
        return CompositeSkill(skills, operator)
