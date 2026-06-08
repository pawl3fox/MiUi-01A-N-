from __future__ import annotations

import re
from pathlib import Path

from contracts.messages import Plan, PlanStep
from core.paths import is_literal_path, resolve_literal_path, user_profile_dir
from core.semantic import (
    INTENT_TO_OPERATION,
    SemanticAction,
    SemanticPlan,
    infer_extension,
    normalize_known_folder,
)


class AmbiguousPathError(ValueError):
    pass


class PlanResolver:
    def resolve(self, semantic: SemanticPlan) -> Plan:
        steps: list[PlanStep] = []
        for index, action in enumerate(semantic.actions, start=1):
            step = self._resolve_action(index, action)
            steps.append(step)
        description = semantic.description or "Выполнение задачи"
        return Plan(description=description, steps=steps)

    def resolve_action(self, step_id: int, action: SemanticAction) -> PlanStep:
        return self._resolve_action(step_id, action)

    def _resolve_action(self, step_id: int, action: SemanticAction) -> PlanStep:
        mapping = INTENT_TO_OPERATION.get(action.intent)
        if mapping is None:
            raise ValueError(f"Неизвестное намерение: {action.intent}")

        module, operation = mapping

        if operation == "write_file":
            path = self._resolve_file_path(action)
            return PlanStep(
                step_id=step_id,
                module=module,
                operation=operation,
                payload={"path": path, "content": action.content or ""},
            )

        if operation == "mkdir":
            path = self._resolve_directory_path(action)
            return PlanStep(
                step_id=step_id,
                module=module,
                operation=operation,
                payload={"path": path},
            )

        if operation == "delete":
            path = self._resolve_any_path(action)
            return PlanStep(
                step_id=step_id,
                module=module,
                operation=operation,
                payload={"path": path},
            )

        if operation == "list_dir":
            path = self._resolve_any_path(action)
            return PlanStep(
                step_id=step_id,
                module=module,
                operation=operation,
                payload={"path": path},
            )

        if operation == "read_file":
            path = self._resolve_any_path(action)
            return PlanStep(
                step_id=step_id,
                module=module,
                operation=operation,
                payload={"path": path},
            )

        if operation == "copy":
            return PlanStep(
                step_id=step_id,
                module=module,
                operation=operation,
                payload={
                    "source": self._resolve_raw_path(action.source or ""),
                    "destination": self._resolve_raw_path(action.destination or ""),
                },
            )

        if operation == "move":
            return PlanStep(
                step_id=step_id,
                module=module,
                operation=operation,
                payload={
                    "source": self._resolve_raw_path(action.source or ""),
                    "destination": self._resolve_raw_path(action.destination or ""),
                },
            )

        raise ValueError(f"Операция не поддерживается резолвером: {operation}")

    def _resolve_file_path(self, action: SemanticAction) -> str:
        directory = self._resolve_directory_base(action)
        name = (action.name or "file").strip().strip("\"'«»")
        extension = infer_extension(action.object_type, name)
        if extension and not name.lower().endswith(extension):
            if "." not in Path(name).name:
                name = f"{name}{extension}"
        return str(directory / name)

    def _resolve_directory_path(self, action: SemanticAction) -> str:
        if action.location and action.location.path:
            return self._resolve_raw_path(action.location.path)
        if action.name:
            base = self._resolve_directory_base(action)
            name = action.name.strip().strip("\"'«»")
            if is_literal_path(name):
                return self._resolve_raw_path(name)
            return str(base / name)
        return str(self._resolve_directory_base(action))

    def _resolve_any_path(self, action: SemanticAction) -> str:
        if action.location and action.location.path:
            return self._resolve_raw_path(action.location.path)
        if action.name:
            raw = action.name.strip()
            if is_literal_path(raw):
                return self._resolve_raw_path(raw)
            return str(self._resolve_directory_base(action) / raw)
        raise ValueError("Не удалось определить путь для действия")

    def _resolve_directory_base(self, action: SemanticAction) -> Path:
        location = action.location
        if location is None:
            return user_profile_dir()

        if location.path:
            path = self._resolve_raw_path(location.path)
            candidate = Path(path)
            if candidate.exists() and candidate.is_dir():
                return candidate
            if candidate.suffix:
                return candidate.parent
            return candidate

        known_folder = normalize_known_folder(location.known_folder)
        if known_folder:
            return self._known_folder_path(known_folder)

        drive = (location.drive or self._default_drive()).upper()
        return Path(f"{drive}:\\")

    def _known_folder_path(self, key: str) -> Path:
        profile = user_profile_dir()
        candidates: dict[str, list[str]] = {
            "downloads": ["Downloads", "Загрузки"],
            "desktop": ["Desktop", "Рабочий стол"],
            "documents": ["Documents", "Документы"],
            "pictures": ["Pictures", "Изображения"],
            "music": ["Music", "Музыка"],
            "videos": ["Videos", "Видео"],
            "user_profile": [""],
        }
        options = candidates.get(key, [key])
        for option in options:
            path = profile if not option else profile / option
            if path.exists():
                return path.resolve()
        fallback = profile if not options[0] else profile / options[0]
        return fallback.resolve()

    def _resolve_raw_path(self, value: str) -> str:
        text = value.strip().strip("\"'«»")
        if not text:
            raise AmbiguousPathError("Пустой путь")

        if text.lower() in {"~", "home", "домой"}:
            return str(user_profile_dir())

        known = normalize_known_folder(text)
        if known:
            return str(self._known_folder_path(known))

        if is_literal_path(text):
            return str(resolve_literal_path(text))

        raise AmbiguousPathError(
            f"Неоднозначный путь '{text}' — требуется разведка через file_ops"
        )

    def _default_drive(self) -> str:
        return user_profile_dir().drive.rstrip(":") or "C"
