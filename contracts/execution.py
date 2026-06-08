from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from contracts.messages import PlanStep


class LogicAction(str, Enum):
    ABORT = "abort"
    COMPLETE = "complete"
    EXECUTE_STEP = "execute_step"
    RESUME_PLAN = "resume_plan"


class LogicDecision(BaseModel):
    analysis: str
    action: LogicAction
    step: PlanStep | None = None
    note: str = ""


class StepExecution(BaseModel):
    step_id: int
    module: str
    operation: str
    payload: dict[str, Any] = Field(default_factory=dict)
    success: bool
    result: dict[str, Any] | None = None
    error: str | None = None
    attempt: int = 1
    source: str = "plan"
    recovery_analysis: str | None = None


class TaskRunResult(BaseModel):
    task_id: str
    goal: str
    status: str
    steps: list[StepExecution] = Field(default_factory=list)
    final_error: str | None = None
    final_analysis: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == "completed"
