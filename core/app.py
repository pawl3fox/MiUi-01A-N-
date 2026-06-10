from __future__ import annotations

import asyncio

from contracts.execution import TaskRunResult
from core.config import AppConfig, load_config
from core.context_store import ContextStore
from core.event_log import EventLog
from core.executor import TaskExecutor
from core.llm_interface import LLMInterface
from core.logic import LogicCore
from core.lm_studio import LMStudioClient
from core.message_bus import SQLiteMessageBus
from core.orchestrator import ModelOrchestrator
from core.recovery import RecoveryAdvisor
from core.registry import ModuleRegistry
from workers.runner import ModuleWorker


class OperatorApp:
    """Главное приложение системы Operator.

    Архитектура:
    - LLM (LLMInterface) — единая точка входа, управляется пользователем
    - Logic (LogicCore) — обрабатывает запросы LLM через [LOGIC_REQUEST]
    - Orchestrator — переключает модели по требованию
    - ContextStore — хранит историю для контекста LLM
    """

    def __init__(self, config: AppConfig | None = None) -> None:
        self.config = config or load_config()
        self.event_log = EventLog(self.config.events_db_path)
        self.context_store = ContextStore(self.config.context_db_path)
        self.bus = SQLiteMessageBus(self.config.queue_db_path)
        self.lm_client = LMStudioClient(self.config.lm_studio)
        self.registry = ModuleRegistry(self.config.modules_path, self.event_log)

        self.orchestrator = ModelOrchestrator(
            self.config, self.lm_client, self.event_log
        )

        # LLM интерфейс — единая точка входа
        self.llm_interface = LLMInterface(
            lm_client=self.lm_client,
            llm_model=self.config.lm_studio.llm_model,
            event_log=self.event_log,
            context_store=self.context_store,
        )

        # Logic ядро — обработчик действий
        self.logic = LogicCore(
            lm_client=self.lm_client,
            logic_model=self.config.lm_studio.logic_model,
            registry=self.registry,
            bus=self.bus,
            event_log=self.event_log,
        )

        # Recovery advisor
        self.recovery = RecoveryAdvisor(
            lm_client=self.lm_client,
            logic_model=self.config.lm_studio.logic_model,
            registry=self.registry,
            event_log=self.event_log,
        )

        # Worker для исполнения шагов
        self._results_queue: asyncio.Queue = asyncio.Queue()
        self.worker = ModuleWorker(
            registry=self.registry,
            bus=self.bus,
            event_log=self.event_log,
            poll_interval_ms=self.config.system.queue_poll_interval_ms,
            results_queue=self._results_queue,
        )

        # Executor для управления выполнением
        self.executor = TaskExecutor(
            logic=self.logic,
            recovery=self.recovery,
            registry=self.registry,
            bus=self.bus,
            event_log=self.event_log,
            lm_client=self.lm_client,
            logic_model=self.config.lm_studio.logic_model,
            llm_model=self.config.lm_studio.llm_model,
            results_queue=self._results_queue,
            task_timeout_seconds=self.config.system.task_timeout_seconds,
            max_recovery_attempts=self.config.system.max_recovery_attempts,
            max_total_steps=self.config.system.max_total_steps,
        )

        self._worker_task: asyncio.Task[None] | None = None
        self.event_log_instance = self.event_log

    async def startup(self) -> None:
        """Инициализация приложения."""
        self.config.data_path.mkdir(parents=True, exist_ok=True)
        self.config.archive_path.mkdir(parents=True, exist_ok=True)
        await self.event_log.initialize()
        await self.context_store.initialize()
        await self.bus.initialize()
        await self.registry.scan()
        await self.orchestrator.initialize()
        self._worker_task = asyncio.create_task(self.worker.start())

    async def shutdown(self) -> None:
        """Выключение приложения."""
        self.worker.stop()
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

    async def handle_message(self, user_message: str, force_action: bool = False) -> str:
        """Обработать сообщение пользователя через LLM.

        Args:
            user_message: Сообщение от пользователя
            force_action: Если True, принудительно выполнить как действие

        Returns:
            Ответ пользователю
        """
        if force_action:
            return await self._execute_logic_request(user_message)

        # LLM сама решает, нужна ли ей Logic
        await self.orchestrator.activate_llm()
        llm_response = await self.llm_interface.process_user_message(user_message)

        # Сохранить в контекст
        await self.context_store.store_event(
            task_id=None,
            channel="user",
            message=user_message[:200],
        )

        # Если LLM запросила выполнение действия
        if llm_response.logic_request:
            task_result = await self._execute_logic_request(
                llm_response.logic_request.task_description
            )

            # Получить финальный ответ от LLM о результате
            result_response = await self.llm_interface.format_task_result(
                user_request=user_message,
                task_status=task_result.status,
                task_summary=self._format_task_result_brief(task_result),
            )

            await self.context_store.store_event(
                task_id=task_result.task_id,
                channel="task",
                message=f"Результат: {task_result.status}",
                payload={"status": task_result.status},
            )

            return result_response

        # Иначе просто вернуть ответ от LLM
        await self.context_store.store_event(
            task_id=None,
            channel="llm",
            message="Chat response",
        )
        return llm_response.text

    async def _execute_logic_request(self, task_description: str) -> TaskRunResult:
        """Выполнить запрос Logic ядра.

        Args:
            task_description: Описание задачи

        Returns:
            Результат выполнения
        """
        await self.orchestrator.activate_logic()
        result = await self.executor.run(task_description)

        await self.context_store.store_event(
            task_id=result.task_id,
            channel="task",
            message=f"Task {result.status}: {task_description[:100]}",
            payload={
                "status": result.status,
                "goal": task_description,
                "steps_count": len(result.steps),
            },
        )

        return result

    def _format_task_result_brief(self, run_result: TaskRunResult) -> str:
        """Краткое описание результата для LLM."""
        if run_result.succeeded:
            paths = self._collect_result_paths(run_result)
            if paths:
                return f"Успешно выполнено. Пути: {', '.join(paths)}"
            return "Успешно выполнено."

        reason = run_result.final_analysis or run_result.final_error or "ошибка"
        return f"Ошибка: {reason}"

    def _collect_result_paths(self, run_result: TaskRunResult) -> list[str]:
        """Собрать список путей из результата."""
        paths: list[str] = []
        for step in run_result.steps:
            if not step.result:
                continue
            path = step.result.get("path") or step.result.get("destination")
            if path and path not in paths:
                paths.append(path)
        return paths
