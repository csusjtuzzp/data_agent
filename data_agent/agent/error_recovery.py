# Copyright (c) Data Agent Team. All rights reserved.
"""Error recovery strategies for sub-agents."""

import asyncio
from enum import Enum
from typing import Any, Optional

from loguru import logger

from data_agent.utils.task import SubTask
from data_agent.agent.base import AgentContext


class ErrorType(Enum):
    """Classification of error types."""

    TRANSIENT = "transient"
    RESOURCE = "resource"
    PARSING = "parsing"
    VALIDATION = "validation"
    UNKNOWN = "unknown"


class RecoveryStrategy(Enum):
    """Available recovery strategies."""

    RETRY = "retry"
    FALLBACK_BACKEND = "fallback_backend"
    FALLBACK_METHOD = "fallback_method"
    SKIP = "skip"
    ABORT = "abort"


DEFAULT_RETRY_CONFIG = {
    "max_attempts": 3,
    "base_delay": 1.0,
    "max_delay": 30.0,
    "backoff_factor": 2.0,
}


class ErrorRecovery:
    """Error recovery strategies for sub-agents."""

    ERROR_STRATEGY_MAP = {
        ErrorType.TRANSIENT: [
            RecoveryStrategy.RETRY,
            RecoveryStrategy.FALLBACK_BACKEND,
        ],
        ErrorType.RESOURCE: [
            RecoveryStrategy.RETRY,
            RecoveryStrategy.FALLBACK_METHOD,
        ],
        ErrorType.PARSING: [
            RecoveryStrategy.FALLBACK_BACKEND,
            RecoveryStrategy.SKIP,
        ],
        ErrorType.VALIDATION: [
            RecoveryStrategy.SKIP,
            RecoveryStrategy.ABORT,
        ],
    }

    def __init__(
        self,
        backend_selector: Optional[Any] = None,
        retry_config: Optional[dict] = None,
    ):
        self.backend_selector = backend_selector
        self.retry_config = retry_config or DEFAULT_RETRY_CONFIG

    def classify_error(self, error: Exception) -> ErrorType:
        """Classify error type from exception."""
        error_msg = str(error).lower()

        transient_keywords = [
            "timeout",
            "connection",
            "network",
            "503",
            "502",
        ]
        if any(kw in error_msg for kw in transient_keywords):
            return ErrorType.TRANSIENT

        resource_keywords = ["memory", "gpu", "cuda", "oom"]
        if any(kw in error_msg for kw in resource_keywords):
            return ErrorType.RESOURCE

        parsing_keywords = ["parse", "extract", "invalid"]
        if any(kw in error_msg for kw in parsing_keywords):
            return ErrorType.PARSING

        validation_keywords = ["validate", "quality", "check"]
        if any(kw in error_msg for kw in validation_keywords):
            return ErrorType.VALIDATION

        return ErrorType.UNKNOWN

    async def recover(
        self,
        agent: Any,
        subtask: SubTask,
        context: AgentContext,
        error: str,
    ) -> Optional[Any]:
        """Attempt to recover from an error."""
        error_type = self.classify_error(Exception(error))
        strategies = self.ERROR_STRATEGY_MAP.get(
            error_type, [RecoveryStrategy.ABORT]
        )

        for strategy in strategies:
            try:
                if strategy == RecoveryStrategy.RETRY:
                    return await self._retry(agent, subtask, context)
                elif strategy == RecoveryStrategy.FALLBACK_BACKEND:
                    return await self._fallback_backend(agent, subtask, context)
                elif strategy == RecoveryStrategy.SKIP:
                    logger.warning(
                        f"Skipping subtask {subtask.subtask_id}"
                    )
                    return None
            except Exception as e:
                logger.error(
                    f"Recovery strategy {strategy} failed: {e}"
                )
                continue

        return None

    async def _retry(
        self,
        agent: Any,
        subtask: SubTask,
        context: AgentContext,
    ) -> Any:
        """Retry with exponential backoff."""
        config = subtask.retry_config or self.retry_config
        max_attempts = config.get("max_attempts", 3)
        base_delay = config.get("base_delay", 1.0)
        backoff_factor = config.get("backoff_factor", 2.0)
        max_delay = config.get("max_delay", 30.0)

        for attempt in range(max_attempts):
            try:
                if attempt > 0:
                    delay = min(
                        base_delay * (backoff_factor**attempt), max_delay
                    )
                    logger.info(
                        f"Retrying after {delay}s "
                        f"(attempt {attempt + 1}/{max_attempts})"
                    )
                    await asyncio.sleep(delay)

                response = await agent.execute(context)
                if response.success:
                    return response.output
            except Exception as e:
                import traceback
                logger.warning(f"Retry attempt {attempt + 1} failed: {traceback.format_exc()}")

    async def _fallback_backend(
        self,
        agent: Any,
        subtask: SubTask,
        context: AgentContext,
    ) -> Any:
        """Fallback to a different backend."""
        if not self.backend_selector:
            return None

        original_backend = context.current_state.get("backend", "pipeline")
        fallback = self.backend_selector.get_fallback_backend(original_backend)

        if fallback == original_backend:
            raise ValueError("No fallback backend available")

        logger.info(f"Falling back from {original_backend} to {fallback}")
        context.current_state["backend"] = fallback

        return await agent.execute(context)
