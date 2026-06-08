from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class LMStudioConfig(BaseModel):
    base_url: str = "http://localhost:1234"
    api_token: str | None = None
    llm_model: str = "qwen/qwen3.5-9b"
    logic_model: str = "deepseek/deepseek-r1-0528-qwen3-8b"


class PathsConfig(BaseModel):
    modules_dir: str = "modules"
    data_dir: str = "data"
    archive_dir: str = "archive/modules"


class SystemConfig(BaseModel):
    queue_poll_interval_ms: int = 100
    task_timeout_seconds: int = 120
    max_recovery_attempts: int = 8
    max_total_steps: int = 24


class AppConfig(BaseModel):
    lm_studio: LMStudioConfig = Field(default_factory=LMStudioConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    system: SystemConfig = Field(default_factory=SystemConfig)
    root_dir: Path = Field(default_factory=lambda: Path.cwd())

    @property
    def modules_path(self) -> Path:
        return self.root_dir / self.paths.modules_dir

    @property
    def data_path(self) -> Path:
        return self.root_dir / self.paths.data_dir

    @property
    def archive_path(self) -> Path:
        return self.root_dir / self.paths.archive_dir

    @property
    def events_db_path(self) -> Path:
        return self.data_path / "events.db"

    @property
    def queue_db_path(self) -> Path:
        return self.data_path / "queue.db"


def load_config(config_path: Path | None = None) -> AppConfig:
    root_dir = Path.cwd()
    path = config_path or root_dir / "config.yaml"
    raw: dict[str, Any] = {}
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
    return AppConfig(root_dir=root_dir, **raw)
