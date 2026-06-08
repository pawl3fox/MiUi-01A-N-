from __future__ import annotations

import os
import re
from pathlib import Path


def user_profile_dir() -> Path:
    return Path(os.environ["USERPROFILE"]).resolve()


def is_literal_path(value: str) -> bool:
    text = value.strip().strip("\"'«»")
    if not text:
        return False
    if text.startswith("%") or text.startswith("~"):
        return True
    if re.match(r"^[A-Za-z]:\\", text) or text.startswith("\\\\"):
        return True
    if "\\" in text or "/" in text:
        return True
    return False


def resolve_literal_path(value: str) -> Path:
    cleaned = value.strip().strip("\"'«»")
    if cleaned.lower() in {"~", "home", "домой"}:
        return user_profile_dir()
    expanded = os.path.expanduser(cleaned)
    expanded = os.path.expandvars(expanded)
    if "%" in expanded:
        raise ValueError(
            f"Путь содержит нераскрытые переменные окружения: {expanded}"
        )
    path = Path(expanded)
    if path.is_absolute():
        return path.resolve()
    return (user_profile_dir() / path).resolve()


def resolve_path(value: str) -> Path:
    return resolve_literal_path(value)
