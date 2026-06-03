# Copyright (c) Data Agent Team. All rights reserved.
"""API schemas for request/response models."""

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class TaskSubmitRequest(BaseModel):
    """Request to submit a new task."""

    instruction: str = Field(..., description="Natural language instruction")
    data: list[dict] = Field(..., description="List of documents to process")
    options: dict = Field(default_factory=dict, description="Processing options")


class TaskStatusResponse(BaseModel):
    """Response for task status query."""

    task_id: str
    status: Literal[
        "pending", "running", "completed", "failed"
    ]
    progress: float = Field(ge=0.0, le=1.0)
    current_step: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    error: Optional[str] = None


class TaskResultResponse(BaseModel):
    """Response for task result."""

    task_id: str
    status: str
    results: dict
    metadata: dict
    download_url: Optional[str] = None


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str
    agents_available: list[str]
    skills_available: list[str]


class CapabilitiesResponse(BaseModel):
    """System capabilities response."""

    supported_formats: list[str]
    supported_backends: list[str]
    max_concurrent_tasks: int
    features: list[str]
