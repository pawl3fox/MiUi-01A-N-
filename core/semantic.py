from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

class SemanticLocation(BaseModel):
    drive: str | None = None
    known_folder: str | None = None
    path: str | None = None


class SemanticAction(BaseModel):
    intent: str
    object_type: str | None = None
    name: str | None = None
    location: SemanticLocation | None = None
    content: str = ""
    source: str | None = None
    destination: str | None = None


class SemanticPlan(BaseModel):
    description: str = ""
    actions: list[SemanticAction] = Field(default_factory=list)


OBJECT_TYPE_EXTENSIONS: dict[str, str] = {
    "text_file": ".txt",
    "text": ".txt",
    "текстовый_файл": ".txt",
    "markdown": ".md",
    "json": ".json",
    "python": ".py",
    "csv": ".csv",
    "directory": "",
    "folder": "",
    "папка": "",
    "директория": "",
}

KNOWN_FOLDER_ALIASES: dict[str, list[str]] = {
    "downloads": ["downloads", "загрузки", "download"],
    "desktop": ["desktop", "рабочий стол", "рабочий_стол"],
    "documents": ["documents", "документы", "document"],
    "pictures": ["pictures", "изображения", "картинки"],
    "music": ["music", "музыка"],
    "videos": ["videos", "видео"],
    "user_profile": [
        "user profile",
        "user_profile",
        "профиль",
        "пользователя",
        "пользователь",
        "домой",
        "home",
    ],
}

INTENT_TO_OPERATION: dict[str, tuple[str, str]] = {
    "create_file": ("file_ops", "write_file"),
    "write_file": ("file_ops", "write_file"),
    "create_directory": ("file_ops", "mkdir"),
    "mkdir": ("file_ops", "mkdir"),
    "delete": ("file_ops", "delete"),
    "list": ("file_ops", "list_dir"),
    "list_dir": ("file_ops", "list_dir"),
    "read": ("file_ops", "read_file"),
    "read_file": ("file_ops", "read_file"),
    "copy": ("file_ops", "copy"),
    "move": ("file_ops", "move"),
}


def infer_extension(object_type: str | None, name: str | None) -> str:
    if not object_type:
        return ""
    normalized = object_type.strip().lower().replace(" ", "_")
    if normalized in OBJECT_TYPE_EXTENSIONS:
        return OBJECT_TYPE_EXTENSIONS[normalized]
    if "текст" in normalized or "text" in normalized:
        return ".txt"
    return ""


def normalize_known_folder(value: str | None) -> str | None:
    if not value:
        return None
    lowered = value.strip().lower()
    for key, aliases in KNOWN_FOLDER_ALIASES.items():
        if lowered == key or lowered in aliases:
            return key
        for alias in aliases:
            if alias in lowered:
                return key
    return None


def parse_semantic_from_text(text: str) -> SemanticPlan | None:
    cleaned = text.strip()
    if not cleaned:
        return None

    create_file = _parse_create_file(cleaned)
    if create_file:
        return SemanticPlan(description=cleaned, actions=[create_file])

    create_dir = _parse_create_directory(cleaned)
    if create_dir:
        return SemanticPlan(description=cleaned, actions=[create_dir])

    delete_action = _parse_delete(cleaned)
    if delete_action:
        return SemanticPlan(description=cleaned, actions=[delete_action])

    list_action = _parse_list(cleaned)
    if list_action:
        return SemanticPlan(description=cleaned, actions=[list_action])

    return None


def _parse_create_file(text: str) -> SemanticAction | None:
    if "файл" not in text.lower():
        return None
    if not re.search(r"(?:создай|создать|сделай|нужно создать)", text, re.I):
        return None

    path_name_match = re.search(
        r"(?:создай|создать|сделай|нужно создать)[-\s\w]*?"
        r"(?:текстовый\s+)?файл\s+(?:в|на)\s+"
        r"[«\"']?([^«\"'\n,]+?)[»\"']?\s*,\s*"
        r"назови(?:\s+его)?\s+[«\"']?([^«\"'\n,.]+)[»\"']?",
        text,
        flags=re.IGNORECASE,
    )
    if path_name_match:
        path = path_name_match.group(1).strip().strip("\"'«»")
        name = path_name_match.group(2).strip().strip("\"'«»")
        object_type = "text_file" if "текстов" in text.lower() else "file"
        return SemanticAction(
            intent="create_file",
            object_type=object_type,
            name=name,
            location=SemanticLocation(path=path),
            content="",
        )

    name_match = re.search(
        r"(?:текстовый\s+)?файл\s+[«\"']?([^«\"'\n,]+?)[»\"']?"
        r"(?:\s+(?:в|на)\b|\s*$)",
        text,
        flags=re.IGNORECASE,
    )
    if not name_match:
        return None

    name = name_match.group(1).strip().strip("\"'«»")
    location = SemanticLocation(
        drive=_extract_drive(text),
        known_folder=_extract_known_folder(text),
    )

    if location.known_folder is None and _mentions_user_profile(text):
        location.known_folder = "user_profile"

    object_type = "text_file" if "текстов" in text.lower() else "file"
    return SemanticAction(
        intent="create_file",
        object_type=object_type,
        name=name,
        location=location,
        content="",
    )


def _parse_create_directory(text: str) -> SemanticAction | None:
    if "файл" in text.lower():
        return None
    if not re.search(r"(?:создай|создать|mkdir)", text, re.I):
        return None

    match = re.search(
        r"(?:папку|директорию|каталог)\s+[«\"']?([^«\"'\n]+)[»\"']?",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    raw = match.group(1).strip()
    return SemanticAction(
        intent="create_directory",
        object_type="directory",
        name=raw,
        location=SemanticLocation(
            path=raw,
            known_folder=_extract_known_folder(text) or normalize_known_folder(raw),
            drive=_extract_drive(text),
        ),
    )


def _parse_delete(text: str) -> SemanticAction | None:
    match = re.search(
        r"(?:удали|удалить|delete)\s+(?:файл|папку|директорию)?\s*"
        r"[«\"']?([^«\"'\n]+)[»\"']?\s*$",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    target = match.group(1).strip()
    return SemanticAction(
        intent="delete",
        name=target,
        location=SemanticLocation(path=target),
    )


def _parse_list(text: str) -> SemanticAction | None:
    match = re.search(
        r"(?:покажи|список|list)\s+(?:содержимое|файлы)?\s*"
        r"(?:папки|директории)?\s*[«\"']?([^«\"'\n]+)[»\"']?\s*$",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    target = match.group(1).strip()
    return SemanticAction(
        intent="list",
        location=SemanticLocation(
            path=target,
            known_folder=_extract_known_folder(target) or normalize_known_folder(target),
        ),
    )


def _extract_drive(text: str) -> str | None:
    match = re.search(r"на\s+диске?\s*([A-Za-z])\b", text, flags=re.IGNORECASE)
    return match.group(1).upper() if match else None


def _extract_known_folder(text: str) -> str | None:
    lowered = text.lower()
    best_key: str | None = None
    best_len = 0
    for key, aliases in KNOWN_FOLDER_ALIASES.items():
        for alias in aliases:
            if alias in lowered and len(alias) > best_len:
                best_key = key
                best_len = len(alias)
    return best_key


def _mentions_user_profile(text: str) -> bool:
    return bool(re.search(r"пользовател", text, flags=re.IGNORECASE))


def semantic_plan_from_llm_json(data: dict[str, Any]) -> SemanticPlan:
    if "actions" in data:
        plan = SemanticPlan.model_validate(data)
        for action in plan.actions:
            if action.location and action.location.known_folder:
                action.location.known_folder = (
                    normalize_known_folder(action.location.known_folder)
                    or action.location.known_folder
                )
        return plan

    steps = data.get("steps", [])
    actions: list[SemanticAction] = []
    for step in steps:
        operation = step.get("operation", "")
        payload = step.get("payload", {})
        intent = _operation_to_intent(operation)
        if not intent:
            continue
        actions.append(
            SemanticAction(
                intent=intent,
                object_type=step.get("object_type"),
                name=_pathish_name(payload),
                location=SemanticLocation(path=payload.get("path")),
                content=payload.get("content", ""),
                source=payload.get("source"),
                destination=payload.get("destination"),
            )
        )
    return SemanticPlan(description=data.get("description", ""), actions=actions)


def _pathish_name(payload: dict[str, Any]) -> str | None:
    path = payload.get("path")
    if isinstance(path, str) and path:
        return path
    return None


def _operation_to_intent(operation: str) -> str | None:
    for intent, (_, op) in INTENT_TO_OPERATION.items():
        if op == operation:
            return intent
    return None
