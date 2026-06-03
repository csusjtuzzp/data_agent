# Copyright (c) Data Agent Team. All rights reserved.
"""Exception hierarchy for Data Agent."""


class DataAgentError(Exception):
    """Base exception for all Data Agent errors."""

    pass


class AgentError(DataAgentError):
    """Base exception for agent-related errors."""

    pass


class SubtaskExecutionError(AgentError):
    """Raised when a subtask fails to execute."""

    def __init__(self, subtask_id: str, original_error: str):
        self.subtask_id = subtask_id
        self.original_error = original_error
        super().__init__(f"Subtask {subtask_id} failed: {original_error}")


class CircularDependencyError(AgentError):
    """Raised when circular dependencies detected in task graph."""

    pass


class SkillError(DataAgentError):
    """Base exception for skill-related errors."""

    pass


class SkillNotFoundError(SkillError):
    """Raised when requested skill is not registered."""

    pass


class SkillExecutionError(SkillError):
    """Raised when skill execution fails."""

    pass


class MinerUIntegrationError(DataAgentError):
    """Base exception for MinerU integration errors."""

    pass


class BackendSelectionError(MinerUIntegrationError):
    """Raised when backend selection fails."""

    pass


class ParsingError(MinerUIntegrationError):
    """Raised when document parsing fails."""

    pass


class ValidationError(DataAgentError):
    """Raised when validation fails."""

    pass


class APIError(DataAgentError):
    """Base exception for API-related errors."""

    pass


class TaskNotFoundError(APIError):
    """Raised when task ID not found."""

    pass


class WebSocketError(APIError):
    """Raised when WebSocket operation fails."""

    pass


class ConfigurationError(DataAgentError):
    """Raised when configuration is invalid."""

    pass


class ErrorRecoveryError(DataAgentError):
    """Raised when error recovery fails."""

    pass
