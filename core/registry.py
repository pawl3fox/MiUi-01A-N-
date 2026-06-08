from __future__ import annotations

import json
from pathlib import Path

from contracts.manifest import ModuleManifest
from core.event_log import EventLog


class ModuleRegistry:
    def __init__(self, modules_dir: Path, event_log: EventLog) -> None:
        self._modules_dir = modules_dir
        self._event_log = event_log
        self._manifests: dict[str, ModuleManifest] = {}

    @property
    def modules(self) -> dict[str, ModuleManifest]:
        return dict(self._manifests)

    async def scan(self) -> None:
        self._manifests.clear()
        if not self._modules_dir.exists():
            self._modules_dir.mkdir(parents=True, exist_ok=True)
            return

        for manifest_path in sorted(self._modules_dir.glob("*/manifest.json")):
            with manifest_path.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
            manifest = ModuleManifest.model_validate(raw)
            manifest_dir = manifest_path.parent
            self._manifests[manifest.name] = manifest
            await self._event_log.log(
                channel="info",
                source="registry",
                message=f"Зарегистрирован модуль {manifest.name} v{manifest.version}",
                payload={"path": str(manifest_dir)},
            )

    def get(self, name: str) -> ModuleManifest | None:
        return self._manifests.get(name)

    def describe_for_logic(self) -> str:
        if not self._manifests:
            return "Доступные модули отсутствуют."

        lines: list[str] = []
        for manifest in self._manifests.values():
            lines.append(f"## {manifest.name} v{manifest.version}")
            lines.append(manifest.description)
            for operation in manifest.operations:
                approval = " [требует подтверждения]" if operation.requires_approval else ""
                lines.append(
                    f"- {operation.name}: {operation.description}{approval}"
                )
                if operation.input_schema:
                    lines.append(f"  input: {json.dumps(operation.input_schema, ensure_ascii=False)}")
            lines.append("")
        return "\n".join(lines)

    def get_module_path(self, name: str) -> Path | None:
        candidate = self._modules_dir / name
        if candidate.exists():
            return candidate
        for path in self._modules_dir.iterdir():
            manifest_path = path / "manifest.json"
            if not manifest_path.exists():
                continue
            with manifest_path.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
            if raw.get("name") == name:
                return path
        return None
