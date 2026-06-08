from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Operation(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    requires_approval: bool = False


class ModuleManifest(BaseModel):
    name: str
    version: str
    description: str
    entrypoint: str = "agent:execute"
    operations: list[Operation] = Field(default_factory=list)

    def get_operation(self, name: str) -> Operation | None:
        for operation in self.operations:
            if operation.name == name:
                return operation
        return None
