from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Any

from core.paths import resolve_path, user_profile_dir


async def execute(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    handlers = {
        "mkdir": _mkdir,
        "write_file": _write_file,
        "read_file": _read_file,
        "delete": _delete,
        "list_dir": _list_dir,
        "copy": _copy,
        "move": _move,
        "exists": _exists,
        "stat": _stat,
        "glob": _glob,
        "well_known_dirs": _well_known_dirs,
    }
    handler = handlers.get(operation)
    if handler is None:
        raise ValueError(f"Неизвестная операция: {operation}")
    return await handler(payload)


async def _well_known_dirs(payload: dict[str, Any]) -> dict[str, Any]:
    profile = user_profile_dir()
    candidates: dict[str, list[Path]] = {
        "user_profile": [profile],
        "downloads": [profile / "Downloads", profile / "Загрузки"],
        "desktop": [profile / "Desktop", profile / "Рабочий стол"],
        "documents": [profile / "Documents", profile / "Документы"],
        "pictures": [profile / "Pictures", profile / "Изображения"],
        "music": [profile / "Music", profile / "Музыка"],
        "videos": [profile / "Videos", profile / "Видео"],
        "program_files": [Path("C:/Program Files")],
        "program_files_x86": [Path("C:/Program Files (x86)")],
    }

    directories: dict[str, dict[str, Any]] = {}
    for key, paths in candidates.items():
        for path in paths:
            resolved = path.resolve()
            directories[key] = {
                "path": str(resolved),
                "exists": resolved.exists(),
                "writable": os.access(resolved, os.W_OK) if resolved.exists() else False,
            }
            if resolved.exists():
                break
    return {"directories": directories}


async def _glob(payload: dict[str, Any]) -> dict[str, Any]:
    root = _resolve_path(payload.get("root", str(user_profile_dir())))
    pattern = payload["pattern"]
    if not root.exists():
        raise FileNotFoundError(f"Корневая директория не найдена: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Не директория: {root}")

    matches = await asyncio.to_thread(
        lambda: sorted(root.glob(pattern))[: int(payload.get("limit", 100))]
    )
    return {
        "root": str(root),
        "pattern": pattern,
        "matches": [str(match) for match in matches],
        "count": len(matches),
    }


async def _stat(payload: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_path(payload["path"])
    if not path.exists():
        raise FileNotFoundError(f"Путь не найден: {path}")

    stat_result = await asyncio.to_thread(path.stat)
    return {
        "path": str(path),
        "exists": True,
        "is_file": path.is_file(),
        "is_dir": path.is_dir(),
        "size": stat_result.st_size,
        "writable": os.access(path, os.W_OK),
    }


async def _mkdir(payload: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_path(payload["path"])
    await asyncio.to_thread(path.mkdir, parents=True, exist_ok=True)
    return {"path": str(path), "created": True}


async def _write_file(payload: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_path(payload["path"])
    content = payload.get("content", "")
    await asyncio.to_thread(_write_text, path, content)
    return {"path": str(path), "bytes_written": len(content.encode("utf-8"))}


async def _read_file(payload: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_path(payload["path"])
    content = await asyncio.to_thread(path.read_text, encoding="utf-8")
    return {"path": str(path), "content": content}


async def _delete(payload: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_path(payload["path"])
    if not path.exists():
        raise FileNotFoundError(f"Путь не найден: {path}")

    if path.is_dir():
        await asyncio.to_thread(shutil.rmtree, path)
    else:
        await asyncio.to_thread(path.unlink)
    return {"path": str(path), "deleted": True}


async def _list_dir(payload: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_path(payload["path"])
    if not path.exists():
        raise FileNotFoundError(f"Путь не найден: {path}")
    if not path.is_dir():
        raise NotADirectoryError(f"Не директория: {path}")

    entries = await asyncio.to_thread(
        lambda: sorted(path.iterdir(), key=lambda item: item.name.lower())
    )
    items = [
        {
            "name": entry.name,
            "path": str(entry),
            "type": "directory" if entry.is_dir() else "file",
        }
        for entry in entries
    ]
    return {"path": str(path), "entries": items}


async def _copy(payload: dict[str, Any]) -> dict[str, Any]:
    source = _resolve_path(payload["source"])
    destination = _resolve_path(payload["destination"])
    if not source.exists():
        raise FileNotFoundError(f"Источник не найден: {source}")

    if source.is_dir():
        await asyncio.to_thread(shutil.copytree, source, destination, dirs_exist_ok=True)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copy2, source, destination)
    return {"source": str(source), "destination": str(destination), "copied": True}


async def _exists(payload: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_path(payload["path"])
    exists = await asyncio.to_thread(path.exists)
    result: dict[str, Any] = {"path": str(path), "exists": exists}
    if exists:
        result["type"] = "directory" if path.is_dir() else "file"
        result["writable"] = os.access(path, os.W_OK)
    return result


async def _move(payload: dict[str, Any]) -> dict[str, Any]:
    source = _resolve_path(payload["source"])
    destination = _resolve_path(payload["destination"])
    if not source.exists():
        raise FileNotFoundError(f"Источник не найден: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(shutil.move, str(source), str(destination))
    return {"source": str(source), "destination": str(destination), "moved": True}


def _resolve_path(value: str) -> Path:
    return resolve_path(value)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
