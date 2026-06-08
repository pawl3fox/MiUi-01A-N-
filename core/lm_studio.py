from __future__ import annotations

from typing import Any

import httpx

from core.config import LMStudioConfig


class LMStudioClient:
    def __init__(self, config: LMStudioConfig) -> None:
        self._config = config
        self._headers: dict[str, str] = {"Content-Type": "application/json"}
        if config.api_token:
            self._headers["Authorization"] = f"Bearer {config.api_token}"

    @property
    def _native_base(self) -> str:
        return self._config.base_url.rstrip("/")

    async def list_models(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(
                f"{self._native_base}/api/v1/models",
                headers=self._headers,
            )
            response.raise_for_status()
            payload = response.json()
        if isinstance(payload, dict):
            return payload.get("models", payload.get("data", []))
        if isinstance(payload, list):
            return payload
        return []

    async def list_loaded_models(self) -> list[dict[str, Any]]:
        loaded: list[dict[str, Any]] = []
        for item in await self.list_models():
            key = item.get("key") or item.get("model") or item.get("id")
            instance_id = item.get("instance_id") or item.get("id")
            is_loaded = item.get("loaded", item.get("status") == "loaded")
            if not is_loaded or not instance_id:
                continue
            loaded.append(
                {
                    "key": key,
                    "instance_id": instance_id,
                    "raw": item,
                }
            )
        return loaded

    async def get_loaded_instance_id(self, model_key: str) -> str | None:
        for item in await self.list_loaded_models():
            if item["key"] == model_key or item["instance_id"] == model_key:
                return item["instance_id"]
        return None

    async def find_loaded_model(self, model_key: str) -> dict[str, Any] | None:
        for item in await self.list_loaded_models():
            if item["key"] == model_key or item["instance_id"] == model_key:
                return item
        return None

    async def load_model(self, model_key: str) -> str:
        async with httpx.AsyncClient(timeout=600.0) as client:
            response = await client.post(
                f"{self._native_base}/api/v1/models/load",
                headers=self._headers,
                json={"model": model_key},
            )
            response.raise_for_status()
            payload = response.json()
        return payload.get("instance_id", model_key)

    async def unload_model(self, instance_id: str) -> None:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self._native_base}/api/v1/models/unload",
                headers=self._headers,
                json={"instance_id": instance_id},
            )
            response.raise_for_status()

    async def unload_all(self) -> None:
        models = await self.list_models()
        for item in models:
            instance_id = item.get("instance_id") or item.get("id")
            loaded = item.get("loaded", item.get("status") == "loaded")
            if instance_id and loaded:
                try:
                    await self.unload_model(instance_id)
                except httpx.HTTPError:
                    continue

    async def chat_completion(
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int = 2048,
        *,
        combine_reasoning: bool = False,
        json_mode: bool = False,
    ) -> str:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                f"{self._native_base}/v1/chat/completions",
                headers=self._headers,
                json=body,
            )
            response.raise_for_status()
            payload = response.json()
        choices = payload.get("choices", [])
        if not choices:
            raise RuntimeError("LM Studio returned an empty response")

        choice = choices[0]
        message = choice.get("message", {})
        content = _extract_message_text(message, combine_reasoning=combine_reasoning)
        if not content:
            content = _extract_message_text(choice, combine_reasoning=combine_reasoning)
        if not content:
            raise RuntimeError("LM Studio returned empty message content")
        return content


def _extract_message_text(
    payload: dict[str, Any],
    *,
    combine_reasoning: bool = False,
) -> str:
    if not payload:
        return ""

    parts: list[str] = []

    content = payload.get("content")
    if isinstance(content, str) and content.strip():
        parts.append(content.strip())
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
            elif isinstance(item, str) and item.strip():
                parts.append(item.strip())

    for key in ("reasoning_content", "reasoning", "text"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            if combine_reasoning or not parts:
                parts.append(value.strip())

    if combine_reasoning:
        return "\n".join(parts).strip()
    return parts[0].strip() if parts else ""
