from __future__ import annotations

import json
import re

_THINK_OPEN = "<" + "think" + ">"
_THINK_CLOSE = "</" + "think" + ">"


def strip_reasoning(text: str) -> str:
    pattern = re.compile(
        re.escape(_THINK_OPEN) + r".*?" + re.escape(_THINK_CLOSE),
        flags=re.DOTALL | re.IGNORECASE,
    )
    cleaned = pattern.sub("", text)
    cleaned = re.sub(
        r"<thinking>.*?</thinking>",
        "",
        cleaned,
        flags=re.DOTALL | re.IGNORECASE,
    )
    return cleaned.strip()


def extract_json(text: str) -> dict:
    candidates = _json_candidates(text)
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
    raise ValueError(
        f"JSON не найден в ответе модели: {last_error}"
        if last_error
        else "JSON не найден в ответе модели"
    )


def _json_candidates(text: str) -> list[str]:
    cleaned = strip_reasoning(text).strip()
    results: list[str] = []

    if cleaned.startswith("```"):
        fenced = re.sub(r"^```(?:json)?\s*", "", cleaned)
        fenced = re.sub(r"\s*```$", "", fenced).strip()
        if fenced:
            results.append(fenced)

    results.append(cleaned)

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        block = cleaned[start : end + 1].strip()
        if block not in results:
            results.append(block)

    for match in re.finditer(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", cleaned, flags=re.DOTALL):
        block = match.group(0).strip()
        if block and block not in results:
            results.append(block)

    unique: list[str] = []
    for item in reversed(results):
        if item not in unique:
            unique.append(item)
    return unique
