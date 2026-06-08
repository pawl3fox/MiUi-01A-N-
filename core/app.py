from __future__ import annotations

import asyncio

from contracts.execution import TaskRunResult
from core.classifier import Intent, IntentClassifier
from core.config import AppConfig, load_config
from core.event_log import EventLog
from core.executor import TaskExecutor
from core.logic import LogicCore
from core.lm_studio import LMStudioClient
from core.message_bus import SQLiteMessageBus
from core.orchestrator import ModelOrchestrator
from core.recovery import RecoveryAdvisor
from core.registry import ModuleRegistry
from workers.runner import ModuleWorker


class OperatorApp:
    def __init__(self, config: AppConfig | None = None) -> None:
        self.config = config or load_config()
        self.event_log = EventLog(self.config.events_db_path)
        self.bus = SQLiteMessageBus(self.config.queue_db_path)
        self.lm_client = LMStudioClient(self.config.lm_studio)
        self.registry = ModuleRegistry(self.config.modules_path, self.event_log)
        self.orchestrator = ModelOrchestrator(
            self.config, self.lm_client, self.event_log
        )
        self.logic = LogicCore(
            lm_client=self.lm_client,
            logic_model=self.config.lm_studio.logic_model,
            registry=self.registry,
            bus=self.bus,
            event_log=self.event_log,
        )
        self.recovery = RecoveryAdvisor(
            lm_client=self.lm_client,
            logic_model=self.config.lm_studio.logic_model,
            registry=self.registry,
            event_log=self.event_log,
        )
        self.classifier = IntentClassifier(
            lm_client=self.lm_client,
            llm_model=self.config.lm_studio.llm_model,
            event_log=self.event_log,
        )
        self._results_queue: asyncio.Queue = asyncio.Queue()
        self.worker = ModuleWorker(
            registry=self.registry,
            bus=self.bus,
            event_log=self.event_log,
            poll_interval_ms=self.config.system.queue_poll_interval_ms,
            results_queue=self._results_queue,
        )
        self.executor = TaskExecutor(
            logic=self.logic,
            recovery=self.recovery,
            registry=self.registry,
            bus=self.bus,
            event_log=self.event_log,
            lm_client=self.lm_client,
            logic_model=self.config.lm_studio.logic_model,
            results_queue=self._results_queue,
            task_timeout_seconds=self.config.system.task_timeout_seconds,
            max_recovery_attempts=self.config.system.max_recovery_attempts,
            max_total_steps=self.config.system.max_total_steps,
        )
        self._worker_task: asyncio.Task[None] | None = None

    async def startup(self) -> None:
        self.config.data_path.mkdir(parents=True, exist_ok=True)
        self.config.archive_path.mkdir(parents=True, exist_ok=True)
        await self.event_log.initialize()
        await self.bus.initialize()
        await self.registry.scan()
        await self.orchestrator.initialize()
        self._worker_task = asyncio.create_task(self.worker.start())

    async def shutdown(self) -> None:
        self.worker.stop()
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

    async def handle_message(self, user_message: str, force_action: bool = False) -> str:
        if force_action:
            return await self.execute_action(user_message)

        await self.orchestrator.activate_llm()
        intent = await self.classifier.classify(user_message)
        if intent == Intent.ACTION:
            return await self.execute_action(user_message)
        return await self.chat(user_message)

    async def chat(self, user_message: str) -> str:
        await self.orchestrator.activate_llm()
        return await self.lm_client.chat_completion(
            model=self.config.lm_studio.llm_model,
            messages=[{"role": "user", "content": user_message}],
            temperature=0.7,
        )

    async def execute_action(self, action_text: str) -> str:
        await self.orchestrator.activate_logic()
        run_result = await self.executor.run(action_text)
        summary = self._format_run_summary(run_result)

        fallback = self._format_fallback_response(action_text, run_result)
        await self.orchestrator.activate_llm()
        try:
            reply = await self.lm_client.chat_completion(
                model=self.config.lm_studio.llm_model,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Отчёт о выполненной задаче.\n"
                            f"Запрос: {action_text}\n"
                            f"Статус: {run_result.status}\n"
                            f"{summary}"
                        ),
                    },
                ],
                temperature=0.3,
                max_tokens=512,
            )
            if reply.strip():
                return reply
        except RuntimeError as exc:
            await self.event_log.log(
                channel="error",
                source="app",
                message=str(exc),
                task_id=run_result.task_id,
            )
        return fallback

    def _format_run_summary(self, run_result: TaskRunResult) -> str:
        lines: list[str] = []
        if run_result.final_analysis:
            lines.append(f"Анализ Logic: {run_result.final_analysis}")
        for step in run_result.steps:
            label = f"[{step.source}] {step.module}.{step.operation}"
            if step.success:
                lines.append(f"Шаг {step.step_id} {label}: успех — {step.result}")
            else:
                lines.append(f"Шаг {step.step_id} {label}: ошибка — {step.error}")
        if run_result.final_error and run_result.status != "completed":
            lines.append(f"Итог: {run_result.final_error}")
        return "\n".join(lines)

    def _format_fallback_response(
        self,
        action_text: str,
        run_result: TaskRunResult,
    ) -> str:
        if run_result.succeeded:
            paths = self._collect_result_paths(run_result)
            if paths:
                joined = "\n".join(f"• {path}" for path in paths)
                return (
                    f"Готово. Задача «{action_text}» выполнена.\n"
                    f"Фактический путь на диске:\n{joined}"
                )
            return f"Готово. Задача «{action_text}» выполнена."

        reason = run_result.final_analysis or run_result.final_error or "неизвестная ошибка"
        paths = self._collect_result_paths(run_result)
        if paths:
            joined = "\n".join(f"• {path}" for path in paths)
            return (
                f"Не удалось выполнить «{action_text}»: {reason}\n"
                f"Затронутые пути:\n{joined}"
            )
        return f"Не удалось выполнить «{action_text}»: {reason}"

    def _collect_result_paths(self, run_result: TaskRunResult) -> list[str]:
        paths: list[str] = []
        for step in run_result.steps:
            if not step.result:
                continue
            path = step.result.get("path") or step.result.get("destination")
            if path and path not in paths:
                paths.append(path)
        return paths
