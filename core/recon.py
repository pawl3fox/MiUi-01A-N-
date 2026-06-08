from __future__ import annotations

import re
from typing import Any

from contracts.messages import PlanStep
from contracts.execution import StepExecution
from core.json_utils import extract_json
from core.lm_studio import LMStudioClient
from core.paths import is_literal_path
from core.semantic import SemanticAction, SemanticLocation, normalize_known_folder


class ReconContext:
    def __init__(self) -> None:
        self.directories: dict[str, dict[str, Any]] = {}
        self.checks: list[dict[str, Any]] = []

    def ingest_step(self, execution: StepExecution) -> None:
        if not execution.success or not execution.result:
            return
        if execution.operation == "well_known_dirs":
            dirs = execution.result.get("directories", {})
            if isinstance(dirs, dict):
                self.directories.update(dirs)
        if execution.operation in {"exists", "stat", "list_dir", "glob"}:
            self.checks.append(execution.result)


def needs_recon(action: SemanticAction, request: str) -> bool:
    if action.intent not in {
        "create_file",
        "write_file",
        "create_directory",
        "mkdir",
        "copy",
        "move",
    }:
        return False

    location = action.location
    if location and location.known_folder:
        return False

    if location and location.path:
        if is_literal_path(location.path):
            return False
        return True

    if re.search(r"пользовател", request, flags=re.IGNORECASE):
        return False

    return location is None or (
        location.known_folder is None and location.path is None
    )


def build_recon_steps(start_id: int = 1) -> list[PlanStep]:
    return [
        PlanStep(
            step_id=start_id,
            module="file_ops",
            operation="well_known_dirs",
            payload={},
        )
    ]


def match_directory(
    request: str,
    action: SemanticAction,
    context: ReconContext,
) -> str | None:
    if not context.directories:
        return None

    hint = _location_hint(request, action)
    compact_hint = _compact(hint)
    if not compact_hint:
        return None

    aliases: dict[str, list[str]] = {
        "program_files_x86": [
            "programfilesx86",
            "programfiles86",
            "programfiles(x86)",
            "x86",
        ],
        "program_files": ["programfiles", "program files"],
        "downloads": ["downloads", "загрузки", "download"],
        "desktop": ["desktop", "рабочийстол", "desktop"],
        "documents": ["documents", "документы"],
        "user_profile": ["userprofile", "пользователя", "профиль", "home"],
        "pictures": ["pictures", "изображения"],
        "music": ["music", "музыка"],
        "videos": ["videos", "видео"],
    }

    best_key: str | None = None
    best_score = 0
    for key, patterns in aliases.items():
        if key not in context.directories:
            continue
        for pattern in patterns:
            pattern_compact = _compact(pattern)
            if not pattern_compact:
                continue
            if pattern_compact in compact_hint or compact_hint in pattern_compact:
                score = len(pattern_compact)
                if score > best_score:
                    best_score = score
                    best_key = key

    if best_key:
        entry = context.directories[best_key]
        return str(entry.get("path", "")) or None
    return None


async def pick_directory_light(
    lm_client: LMStudioClient,
    logic_model: str,
    request: str,
    action: SemanticAction,
    context: ReconContext,
) -> str | None:
    if not context.directories:
        return None

    compact_dirs = {
        key: value.get("path")
        for key, value in context.directories.items()
        if value.get("path")
    }
    hint = _location_hint(request, action)
    user_prompt = (
        "Выбери directory_key для задачи. Ответ — только JSON: "
        '{"directory_key": "ключ"} или {"directory_key": null}\n'
        f"Запрос: {request}\n"
        f"Подсказка локации: {hint}\n"
        f"Доступные ключи: {compact_dirs}"
    )

    try:
        content = await lm_client.chat_completion(
            model=logic_model,
            messages=[{"role": "user", "content": user_prompt}],
            temperature=0.0,
            max_tokens=64,
            combine_reasoning=False,
            json_mode=True,
        )
        data = extract_json(content)
        key = data.get("directory_key")
        if key and key in context.directories:
            return str(context.directories[key].get("path", "")) or None
    except Exception:
        return None
    return None


def apply_resolved_directory(
    action: SemanticAction,
    directory_path: str,
) -> SemanticAction:
    location = action.location or SemanticLocation()
    return action.model_copy(
        update={
            "location": location.model_copy(
                update={"path": directory_path, "known_folder": None}
            )
        }
    )


def _location_hint(request: str, action: SemanticAction) -> str:
    if action.location and action.location.path:
        return action.location.path
    match = re.search(
        r"(?:в|на)\s+([^,\n]+?)(?:,|\s+назови|\s*$)",
        request,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()
    folder = normalize_known_folder(request)
    return folder or request


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9а-яё]", "", value.lower())
