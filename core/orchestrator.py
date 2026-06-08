from __future__ import annotations

from core.config import AppConfig
from core.event_log import EventLog
from core.lm_studio import LMStudioClient
from contracts.messages import ActiveRole


class ModelOrchestrator:
    def __init__(
        self,
        config: AppConfig,
        lm_client: LMStudioClient,
        event_log: EventLog,
    ) -> None:
        self._config = config
        self._lm_client = lm_client
        self._event_log = event_log
        self._active_role = ActiveRole.SWITCHING
        self._loaded_model: str | None = None
        self._loaded_instance_id: str | None = None

    @property
    def active_role(self) -> ActiveRole:
        return self._active_role

    @property
    def loaded_model(self) -> str | None:
        return self._loaded_model

    async def initialize(self) -> None:
        await self._event_log.log(
            channel="info",
            source="orchestrator",
            message="Инициализация оркестратора, загрузка LLM",
        )
        await self.activate_llm()

    async def activate_llm(self) -> None:
        await self._switch_to(
            role=ActiveRole.LLM,
            model_key=self._config.lm_studio.llm_model,
            label="LLM",
        )

    async def activate_logic(self) -> None:
        await self._switch_to(
            role=ActiveRole.LOGIC,
            model_key=self._config.lm_studio.logic_model,
            label="Logic",
        )

    async def _switch_to(self, role: ActiveRole, model_key: str, label: str) -> None:
        if self._active_role == role and self._loaded_model == model_key:
            return

        already_loaded = await self._lm_client.find_loaded_model(model_key)
        if already_loaded:
            other_models = await self._lm_client.list_loaded_models()
            for item in other_models:
                other_id = item["instance_id"]
                if other_id == already_loaded["instance_id"]:
                    continue
                try:
                    await self._lm_client.unload_model(other_id)
                except Exception as exc:
                    await self._event_log.log(
                        channel="error",
                        source="orchestrator",
                        message=f"Ошибка выгрузки модели {item['key']}: {exc}",
                    )

            self._loaded_instance_id = already_loaded["instance_id"]
            self._loaded_model = model_key
            self._active_role = role
            await self._event_log.log(
                channel="info",
                source="orchestrator",
                message=f"Модель {label} уже загружена на сервере",
                payload={
                    "model": model_key,
                    "instance_id": already_loaded["instance_id"],
                },
            )
            return

        self._active_role = ActiveRole.SWITCHING
        await self._event_log.log(
            channel="info",
            source="orchestrator",
            message=f"Переключение на {label} ({model_key})",
        )

        if self._loaded_instance_id:
            try:
                await self._lm_client.unload_model(self._loaded_instance_id)
            except Exception as exc:
                await self._event_log.log(
                    channel="error",
                    source="orchestrator",
                    message=f"Ошибка выгрузки модели: {exc}",
                )
            self._loaded_instance_id = None
            self._loaded_model = None

        for item in await self._lm_client.list_loaded_models():
            try:
                await self._lm_client.unload_model(item["instance_id"])
            except Exception as exc:
                await self._event_log.log(
                    channel="error",
                    source="orchestrator",
                    message=f"Ошибка выгрузки модели {item['key']}: {exc}",
                )

        instance_id = await self._lm_client.load_model(model_key)
        self._loaded_instance_id = instance_id
        self._loaded_model = model_key
        self._active_role = role
        await self._event_log.log(
            channel="info",
            source="orchestrator",
            message=f"Активна модель {label}",
            payload={"model": model_key, "instance_id": instance_id},
        )
