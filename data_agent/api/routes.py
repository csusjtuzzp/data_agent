# Copyright (c) Data Agent Team. All rights reserved.
"""API routes for the Data Agent service."""

import asyncio
import hashlib
import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from starlette.requests import Request

from data_agent.agent.base import AgentContext, BaseAgent
from data_agent.api.schemas import (
    TaskStatusResponse,
    TaskResultResponse,
    HealthResponse,
    CapabilitiesResponse,
)
from data_agent.api.task_storage import TaskStorage

router = APIRouter()

# In-memory task storage for API responses
_api_task_storage = TaskStorage()

_MAIN_AGENT: Optional[BaseAgent] = None


def set_main_agent(agent: BaseAgent) -> None:
    """Set the main agent instance for the router."""
    global _MAIN_AGENT
    _MAIN_AGENT = agent


@router.post("/tasks", response_model=dict)
async def submit_task(
    instruction: str = Form(..., description="Natural language instruction"),
    file: UploadFile = File(..., description="File to process"),
    options: Optional[str] = Form(default="{}", description="Processing options as JSON string"),
) -> dict:
    """Submit a new data processing task with file upload."""
    task_id = str(uuid.uuid4())

    # Save uploaded file to temporary location
    temp_dir = "/tmp/data_agent_uploads"
    os.makedirs(temp_dir, exist_ok=True)
    file_path = os.path.join(temp_dir, f"{task_id}_{file.filename}")

    try:
        content = await file.read()
        with open(file_path, "wb") as f:
            f.write(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {e}")

    # Parse options
    import json
    try:
        options_dict = json.loads(options) if options else {}
    except json.JSONDecodeError:
        options_dict = {}

    context = AgentContext(
        task_id=task_id,
        original_input={
            "instruction": instruction,
            "data": [{"path": file_path, "filename": file.filename}],
            "options": options_dict,
        },
    )

    _api_task_storage.create_task(task_id, file_path=file_path)

    asyncio.create_task(execute_task(task_id, context))

    return {
        "task_id": task_id,
        "status_url": f"/tasks/{task_id}",
    }


@router.get("/tasks/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str) -> TaskStatusResponse:
    """Get task status."""
    task_info = _api_task_storage.get_task(task_id)
    if not task_info:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskStatusResponse(**task_info)


@router.get("/tasks/{task_id}/result", response_model=TaskResultResponse)
async def get_task_result(task_id: str, request: Request) -> TaskResultResponse:
    """Get task result with download URL for output package."""
    task_info = _api_task_storage.get_task(task_id)
    if not task_info:
        raise HTTPException(status_code=404, detail="Task not found")

    status = task_info["status"]
    if status == "failed":
        raise HTTPException(
            status_code=400,
            detail=f"Task failed: {task_info.get('error', 'Unknown error')}",
        )
    if status != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Task not yet completed, current status: {status}",
        )

    # Compute output_dir from original file path
    output_dir = None
    download_url = None

    # Get original input data to find file path
    file_path = task_info.get("file_path")
    if file_path and os.path.exists(file_path):
        # Compute SHA256 hash of file content
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
        file_hash = hasher.hexdigest()

        # Extract original filename (remove UUID prefix if present)
        source_filename = Path(file_path).name
        parts = source_filename.split("_", 1)
        if len(parts) > 1 and parts[0].count("-") == 4 and len(parts[0]) == 36:
            original_filename = parts[1]
        else:
            original_filename = source_filename

        # Compute output directory path
        output_dir = Path("./output") / f"{file_hash[:16]}-{original_filename}"

        # Create zip package
        if output_dir.exists():
            import zipfile

            zip_filename = f"{file_hash[:16]}-{original_filename}.zip"
            zip_path = output_dir / zip_filename

            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file_path in output_dir.rglob("*"):
                    if file_path.is_file():
                        arcname = file_path.relative_to(output_dir)
                        zipf.write(file_path, arcname)

            # Generate download URL
            base_url = str(request.base_url).rstrip("/")
            download_url = f"{base_url}/tasks/{task_id}/download"

    return TaskResultResponse(
        task_id=task_id,
        status=status,
        results=task_info.get("result", {}),
        metadata={"created_at": task_info["created_at"].isoformat()},
        download_url=download_url,
    )


@router.get("/tasks/{task_id}/download")
async def download_result(task_id: str) -> FileResponse:
    """Download the output package as a zip file."""
    task_info = _api_task_storage.get_task(task_id)
    if not task_info:
        raise HTTPException(status_code=404, detail="Task not found")

    status = task_info["status"]
    if status != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Task not yet completed, current status: {status}",
        )

    # Get original input data to find file path
    file_path = task_info.get("file_path")
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Original file not found")

    # Compute SHA256 hash of file content
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    file_hash = hasher.hexdigest()

    # Extract original filename (remove UUID prefix if present)
    source_filename = Path(file_path).name
    parts = source_filename.split("_", 1)
    if len(parts) > 1 and parts[0].count("-") == 4 and len(parts[0]) == 36:
        original_filename = parts[1]
    else:
        original_filename = source_filename

    # Compute output directory path
    output_dir = Path("./output") / f"{file_hash[:16]}-{original_filename}"

    # Find or create zip package
    zip_filename = f"{file_hash[:16]}-{original_filename}.zip"
    zip_path = output_dir / zip_filename

    if not zip_path.exists():
        if output_dir.exists():
            import zipfile
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for fpath in output_dir.rglob("*"):
                    if fpath.is_file():
                        arcname = fpath.relative_to(output_dir)
                        zipf.write(fpath, arcname)
        else:
            raise HTTPException(status_code=404, detail="Output directory not found")

    return FileResponse(
        path=zip_path,
        filename=zip_filename,
        media_type="application/zip",
    )


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse(
        status="healthy",
        version="1.0.0",
        agents_available=[
            "MainAgent",
            "DocumentParser",
            "StructureProcessor",
            "QualityValidator",
        ],
        skills_available=["ParseSkill", "FormatSkill", "FilterSkill"],
    )


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def get_capabilities() -> CapabilitiesResponse:
    """Get system capabilities."""
    return CapabilitiesResponse(
        supported_formats=[".pdf", ".docx", ".pptx", ".xlsx", ".html"],
        supported_backends=[
            "pipeline",
            "vlm-auto-engine",
            "vlm-http-client",
            "hybrid-auto-engine",
            "hybrid-http-client",
            "office",
        ],
        max_concurrent_tasks=10,
        features=[
            "llm_planning",
            "auto_backend_selection",
            "error_recovery",
            "skill_composition",
            "resource_monitoring",
            "circuit_breaker",
            "persistent_storage",
        ],
    )


async def execute_task(task_id: str, context: AgentContext) -> None:
    """Execute task and update status."""
    from loguru import logger

    try:
        _api_task_storage.update_status(task_id, "running", progress=0.0)

        main_agent = _MAIN_AGENT
        if main_agent:
            response = await main_agent.execute(context)

            if response.success:
                _api_task_storage.update_status(
                    task_id, "completed", progress=1.0, result=response.output
                )
                logger.info(f"Task {task_id} completed successfully")
            else:
                logger.warning(f"Task {task_id} failed: {response.error}")
                _api_task_storage.update_status(
                    task_id, "failed", error=response.error
                )
        else:
            logger.error("Main agent not initialized")
            _api_task_storage.update_status(
                task_id, "failed", error="Main agent not initialized"
            )

    except Exception as e:
        import traceback
        logger.error(f"Task {task_id} execution error: {traceback.format_exc()}")
        _api_task_storage.update_status(task_id, "failed", error=str(e))
