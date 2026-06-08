from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class ActiveRole(str, Enum):
    LLM = "llm"
    LOGIC = "logic"
    SWITCHING = "switching"


class PlanStep(BaseModel):
    step_id: int
    module: str
    operation: str
    payload: dict[str, Any] = Field(default_factory=dict)


class Plan(BaseModel):
    task_id: str = Field(default_factory=lambda: str(uuid4()))
    description: str
    steps: list[PlanStep] = Field(default_factory=list)


class TaskMessage(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    parent_task_id: str
    step_id: int
    module: str
    operation: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ResultMessage(BaseModel):
    task_id: str
    step_id: int
    success: bool
    result: dict[str, Any] | None = None
    error: str | None = None


class EventRecord(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    channel: str
    source: str
    message: str
    task_id: str | None = None
    payload: dict[str, Any] | None = None
