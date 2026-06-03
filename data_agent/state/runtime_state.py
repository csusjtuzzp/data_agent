"""
RuntimeState - Central state management based on middle_json.

This is the ONLY state center - all components read from and write to RuntimeState.
middle_json is the sole source of truth for document data.
"""

from pydantic import BaseModel, Field, computed_field
from typing import Any, Optional
from datetime import datetime
from enum import Enum


class ProcessingStatus(Enum):
    IDLE = "idle"
    PARSING = "parsing"
    VALIDATING = "validating"
    REPLANNING = "replanning"
    RECOVERING = "recovering"
    COMPLETED = "completed"
    FAILED = "failed"


class BlockType(Enum):
    TEXT = "text"
    TITLE = "title"
    IMAGE = "image"
    TABLE = "table"
    FIGURE = "figure"
    EQUATION = "equation"
    LIST = "list"
    PAGE_NUMBER = "page_number"
    HEADER = "header"
    FOOTER = "footer"
    SEPARATOR = "separator"
    UNKNOWN = "unknown"


class Block(BaseModel):
    """A single block in middle_json."""
    type: str
    bbox: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    index: int = 0
    lines: list[dict] = Field(default_factory=list)
    blocks: list[dict] = Field(default_factory=list)
    text: Optional[str] = None
    confidence: float = 1.0

    @computed_field
    @property
    def block_type(self) -> str:
        try:
            return BlockType(self.type).value
        except ValueError:
            return BlockType.UNKNOWN.value

    @computed_field
    @property
    def is_layout_element(self) -> bool:
        """Check if this is a layout element (not content)."""
        layout_types = {"page_number", "header", "page_footnote", "page_footer", "separator", "aside_text"}
        return self.type in layout_types


class PageInfo(BaseModel):
    """Per-page information structure."""
    preproc_blocks: list[Block] = Field(default_factory=list)
    para_blocks: list[dict] = Field(default_factory=list)
    discarded_blocks: list[dict] = Field(default_factory=list)
    page_size: tuple[float, float] = Field(default_factory=lambda: (0.0, 0.0))
    page_idx: int = 0

    @computed_field
    @property
    def has_content(self) -> bool:
        """Check if page has meaningful content.

        Checks both preproc_blocks (PDF format) and para_blocks (office format).
        A page has content if it has any non-layout blocks in either structure.
        """
        # Check preproc_blocks (PDF format)
        if any(not b.is_layout_element for b in self.preproc_blocks):
            return True
        # Check para_blocks (office format - docx/pptx/xlsx)
        if self.para_blocks:
            return True
        return False

    @computed_field
    @property
    def meaningful_block_count(self) -> int:
        """Count non-layout blocks in preproc_blocks plus para_blocks count."""
        preproc_count = sum(1 for b in self.preproc_blocks if not b.is_layout_element)
        return preproc_count + len(self.para_blocks)

    @computed_field
    @property
    def block_count_by_type(self) -> dict[str, int]:
        """Get block type distribution."""
        counts = {}
        for b in self.preproc_blocks:
            counts[b.type] = counts.get(b.type, 0) + 1
        return counts


class MiddleJson(BaseModel):
    """
    The canonical middle_json structure.

    Wraps MinerU's middle_json format with full type validation.
    """
    pdf_info: list[PageInfo] = Field(default_factory=list)
    _backend: str = "unknown"
    _version_name: str = "1.0.0"

    model_config = {"extra": "allow"}

    @computed_field
    @property
    def page_count(self) -> int:
        return len(self.pdf_info)

    @computed_field
    @property
    def total_blocks(self) -> int:
        """Total blocks = preproc_blocks (PDF) + para_blocks (office)."""
        return sum(len(p.preproc_blocks) + len(p.para_blocks) for p in self.pdf_info)

    @computed_field
    @property
    def empty_pages(self) -> list[int]:
        """List of page indices that have no content."""
        return [p.page_idx for p in self.pdf_info if not p.has_content]

    @computed_field
    @property
    def discarded_ratio(self) -> float:
        """Ratio of discarded blocks to total blocks."""
        total = self.total_blocks
        if total == 0:
            return 0.0
        discarded = sum(len(p.discarded_blocks) for p in self.pdf_info)
        return discarded / (total + discarded)


class ValidatorOutput(BaseModel):
    """
    Standard validator output protocol.

    All validators must return this structure to enable:
    - Consistent recovery planning
    - Root cause analysis
    - Action recommendation
    """
    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    grade: str

    root_causes: list[str] = Field(default_factory=list)
    recommended_actions: list["RecommendedAction"] = Field(default_factory=list)

    issues: list["ValidatorIssue"] = Field(default_factory=list)
    summary: "ValidationSummary" = Field(default_factory=lambda: ValidationSummary())

    validator_name: str = "QualityValidator"
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    filtered_json: Optional[dict] = None

    model_config = {"extra": "allow"}


class IssueLevel(Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class ValidatorIssue(BaseModel):
    """A single issue found during validation."""
    level: IssueLevel
    page: int = -1
    block_index: int = -1
    issue_type: str
    detail: str
    suggestion: str = ""


class RecommendedAction(BaseModel):
    """A recommended action to address validation issues."""
    action_id: str
    skill_name: str
    priority: int = 0
    target_pages: list[int] = Field(default_factory=list)
    params: dict = Field(default_factory=dict)
    rationale: str


class ValidationSummary(BaseModel):
    """Summary statistics of the validation."""
    total_pages: int = 0
    total_blocks: int = 0
    block_types: dict = Field(default_factory=dict)
    empty_pages: int = 0
    discarded_ratio: float = 0.0
    text_pages: int = 0
    table_count: int = 0
    image_count: int = 0


class RecoveryAction(BaseModel):
    """A single action in a recovery plan."""
    action_id: str
    skill_name: str
    params: dict = Field(default_factory=dict)
    rationale: str
    priority: int = 0


class RecoveryPlan(BaseModel):
    """Complete recovery plan with recommended actions."""
    root_cause: str
    confidence: float
    recommended_actions: list[RecoveryAction]
    estimated_success_rate: float
    fallback_plan: Optional["RecoveryPlan"] = None


class RuntimeState(BaseModel):
    """
    Central state management for the data agent.

    This is the ONLY state center - all components read from and write to RuntimeState.
    It wraps middle_json and provides reactive updates.
    """
    task_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    middle_json: MiddleJson = Field(default_factory=MiddleJson)
    status: ProcessingStatus = ProcessingStatus.IDLE

    current_goals: list[str] = Field(default_factory=list)
    completed_goals: list[str] = Field(default_factory=list)
    failed_goals: list[str] = Field(default_factory=list)

    pending_actions: list[str] = Field(default_factory=list)
    executed_actions: list[str] = Field(default_factory=list)

    last_validation: Optional[dict] = None
    validation_history: list[dict] = Field(default_factory=list)

    observation_summary: Optional[str] = None
    reflection_notes: list[str] = Field(default_factory=list)

    recovery_attempts: int = 0
    max_recovery_attempts: int = 3

    metadata: dict = Field(default_factory=dict)

    # Source file path for re-parsing bad pages
    source_file: Optional[str] = None

    original_instruction: Optional[str] = None

    # Goal binding from replanning - used by ActionSelector
    current_goal_skill: Optional[str] = None
    current_goal_params: dict = Field(default_factory=dict)

    # Track if document tree has been generated
    document_tree_generated: bool = False

    model_config = {"extra": "allow"}

    @computed_field
    @property
    def page_count(self) -> int:
        return len(self.middle_json.pdf_info)

    @computed_field
    @property
    def total_blocks(self) -> int:
        return self.middle_json.total_blocks

    @computed_field
    @property
    def is_terminal(self) -> bool:
        """Check if state is in a terminal status."""
        return self.status in {ProcessingStatus.COMPLETED, ProcessingStatus.FAILED}

    def update_middle_json(self, new_json: dict) -> None:
        """Update middle_json with normalization, preserving _file_path."""
        # Preserve _file_path across updates
        file_path = new_json.get("_file_path") or getattr(self.middle_json, '_file_path', None)
        self.middle_json = MiddleJson(**new_json)
        if file_path:
            self.middle_json._file_path = file_path
        self.updated_at = datetime.utcnow()

    def update_status(self, new_status: ProcessingStatus) -> None:
        """Update processing status."""
        self.status = new_status
        self.updated_at = datetime.utcnow()

    def add_goal(self, goal: str) -> None:
        """Add a new goal to achieve."""
        if goal not in self.current_goals:
            self.current_goals.append(goal)

    def complete_goal(self, goal: str) -> None:
        """Mark a goal as completed."""
        if goal in self.current_goals:
            self.current_goals.remove(goal)
        if goal not in self.completed_goals:
            self.completed_goals.append(goal)
        self.updated_at = datetime.utcnow()

    def fail_goal(self, goal: str) -> None:
        """Mark a goal as failed."""
        if goal in self.current_goals:
            self.current_goals.remove(goal)
        if goal not in self.failed_goals:
            self.failed_goals.append(goal)
        self.updated_at = datetime.utcnow()

    def add_pending_action(self, action: str) -> None:
        """Add an action to the pending queue."""
        if action not in self.pending_actions:
            self.pending_actions.append(action)

    def mark_action_executed(self, action: str) -> None:
        """Mark an action as executed."""
        if action in self.pending_actions:
            self.pending_actions.remove(action)
        if action not in self.executed_actions:
            self.executed_actions.append(action)

    def should_retry(self) -> bool:
        """Check if recovery should be attempted."""
        return self.recovery_attempts < self.max_recovery_attempts

    def get_source_file(self) -> Optional[str]:
        """Get source file with fallback to middle_json._file_path."""
        if self.source_file:
            return self.source_file
        return getattr(self.middle_json, '_file_path', None)

    def is_doc_tree_generated(self) -> bool:
        """Check if document tree has already been generated for this source file."""
        if self.document_tree_generated:
            return True
        source_file = self.get_source_file()
        if source_file and source_file != "N/A":
            from pathlib import Path
            import os
            basename = Path(source_file).stem
            output_dir = "/home/transwarp/Desktop/project/MinerU/result"
            json_out = os.path.join(output_dir, f"{basename}_doc_tree.json")
            return os.path.exists(json_out)
        return False


