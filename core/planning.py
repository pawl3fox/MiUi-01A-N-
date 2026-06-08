from __future__ import annotations

from uuid import uuid4

from pydantic import BaseModel, Field

from contracts.messages import PlanStep
from core.semantic import SemanticAction


class ExecutionBundle(BaseModel):
    task_id: str = Field(default_factory=lambda: str(uuid4()))
    goal: str
    description: str = ""
    recon_steps: list[PlanStep] = Field(default_factory=list)
    deferred_actions: list[SemanticAction] = Field(default_factory=list)
    ready_steps: list[PlanStep] = Field(default_factory=list)
