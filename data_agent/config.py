# Copyright (c) Data Agent Team. All rights reserved.
"""Configuration management."""

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class AgentConfig:
    """Agent configuration."""

    max_concurrency: int = 4
    retry_max_attempts: int = 3
    retry_base_delay: float = 1.0
    task_ttl_hours: int = 24


@dataclass
class MinerUConfig:
    """MinerU configuration."""

    api_url: Optional[str] = ""
    timeout: float = 3600.0
    default_backend: str = "hybrid-auto-engine"
    default_language: str = "ch"


@dataclass
class LLMConfig:
    """LLM configuration."""

    provider: str = "openai"
    model: str = "deepseek-v4-flash"
    api_base: Optional[str] = ""
    api_key: Optional[str] = ""
    max_tokens: int = 40960
    temperature: float = 0.01


@dataclass
class ServerConfig:
    """Server configuration."""

    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"


def load_config() -> dict:
    """Load configuration from environment variables."""
    return {
        "agent": AgentConfig(
            max_concurrency=int(
                os.getenv("DATA_AGENT_MAX_CONCURRENCY", "4")
            ),
            retry_max_attempts=int(
                os.getenv("DATA_AGENT_RETRY_MAX_ATTEMPTS", "3")
            ),
            retry_base_delay=float(
                os.getenv("DATA_AGENT_RETRY_BASE_DELAY", "1.0")
            ),
            task_ttl_hours=int(os.getenv("DATA_AGENT_TASK_TTL_HOURS", "24")),
        ),
        "mineru": MinerUConfig(
            api_url=os.getenv("MINERU_API_URL"),
            timeout=float(os.getenv("MINERU_TIMEOUT", "3600.0")),
            default_backend=os.getenv("MINERU_DEFAULT_BACKEND", "hybrid-auto-engine"),
            default_language=os.getenv("MINERU_DEFAULT_LANGUAGE", "ch"),
        ),
        "llm": LLMConfig(
            provider=os.getenv("LLM_PROVIDER", "openai"),
            model=os.getenv("LLM_MODEL", "deepseek-v4-flash"),
            api_base=os.getenv("LLM_API_BASE", ""),
            api_key=os.getenv("LLM_API_KEY", ""),
        ),
        "server": ServerConfig(
            host=os.getenv("DATA_AGENT_HOST", "0.0.0.0"),
            port=int(os.getenv("DATA_AGENT_PORT", "8888")),
            log_level=os.getenv("DATA_AGENT_LOG_LEVEL", "INFO"),
        ),
    }
