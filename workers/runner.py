from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from typing import Awaitable, Callable

from contracts.messages import ResultMessage, TaskMessage
from core.event_log import EventLog
from core.message_bus import QUEUE_RESULTS, QUEUE_TASKS, MessageBus
from core.registry import ModuleRegistry

ExecuteFn = Callable[[str, dict], Awaitable[dict]]


class ModuleWorker:
    def __init__(
        self,
        registry: ModuleRegistry,
        bus: MessageBus,
        event_log: EventLog,
        poll_interval_ms: int = 100,
        results_queue: asyncio.Queue[ResultMessage] | None = None,
    ) -> None:
        self._registry = registry
        self._bus = bus
        self._event_log = event_log
        self._poll_interval = poll_interval_ms / 1000
        self._results_queue = results_queue
        self._handlers: dict[str, ExecuteFn] = {}
        self._running = False

    async def start(self) -> None:
        self._running = True
        await self._event_log.log(
            channel="info",
            source="worker",
            message="Worker запущен",
        )
        while self._running:
            item = await self._bus.consume(QUEUE_TASKS)
            if item is None:
                await asyncio.sleep(self._poll_interval)
                continue
            message_id, payload = item
            task = TaskMessage.model_validate(payload)
            try:
                result = await self._process_task(task)
            except Exception as exc:
                result = ResultMessage(
                    task_id=task.parent_task_id,
                    step_id=task.step_id,
                    success=False,
                    error=str(exc),
                )
                await self._event_log.log(
                    channel="error",
                    source="worker",
                    message=str(exc),
                    task_id=task.parent_task_id,
                    payload=task.model_dump(),
                )
            await self._bus.publish(QUEUE_RESULTS, result)
            if self._results_queue is not None:
                await self._results_queue.put(result)
            await self._bus.ack(message_id)

    def stop(self) -> None:
        self._running = False

    async def _process_task(self, task: TaskMessage) -> ResultMessage:
        manifest = self._registry.get(task.module)
        if manifest is None:
            raise ValueError(f"Модуль '{task.module}' не зарегистрирован")

        operation = manifest.get_operation(task.operation)
        if operation is None:
            raise ValueError(
                f"Операция '{task.operation}' отсутствует в модуле '{task.module}'"
            )

        if operation.requires_approval and not await self._ask_approval(task):
            await self._event_log.log(
                channel="info",
                source="worker",
                message="Операция отклонена пользователем",
                task_id=task.parent_task_id,
                payload=task.model_dump(),
            )
            return ResultMessage(
                task_id=task.parent_task_id,
                step_id=task.step_id,
                success=False,
                error="Операция отклонена пользователем",
            )

        await self._event_log.log(
            channel="task",
            source="worker",
            message=f"Выполняется {task.module}.{task.operation}",
            task_id=task.parent_task_id,
            payload=task.model_dump(),
        )

        handler = await self._load_handler(task.module, manifest.entrypoint)
        output = await handler(task.operation, task.payload)
        await self._event_log.log(
            channel="task",
            source="worker",
            message=f"Завершено {task.module}.{task.operation}",
            task_id=task.parent_task_id,
            payload=output,
        )
        return ResultMessage(
            task_id=task.parent_task_id,
            step_id=task.step_id,
            success=True,
            result=output,
        )

    async def _ask_approval(self, task: TaskMessage) -> bool:
        prompt = (
            f"\nПодтвердить операцию {task.module}.{task.operation}?\n"
            f"Данные: {task.payload}\n"
            f"[y/N]: "
        )
        answer = await asyncio.to_thread(input, prompt)
        return answer.strip().lower() in {"y", "yes", "д", "да"}

    async def _load_handler(self, module_name: str, entrypoint: str) -> ExecuteFn:
        if module_name in self._handlers:
            return self._handlers[module_name]

        module_path = self._registry.get_module_path(module_name)
        if module_path is None:
            raise ValueError(f"Путь к модулю '{module_name}' не найден")

        file_name, function_name = entrypoint.split(":", 1)
        file_path = module_path / f"{file_name}.py"
        spec = importlib.util.spec_from_file_location(
            f"operator_module_{module_name}",
            file_path,
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Не удалось загрузить модуль из {file_path}")

        module = importlib.util.module_from_spec(spec)
        await asyncio.to_thread(spec.loader.exec_module, module)
        handler = getattr(module, function_name, None)
        if handler is None:
            raise AttributeError(f"Функция '{function_name}' не найдена в {file_path}")
        if not asyncio.iscoroutinefunction(handler):
            raise TypeError(f"Функция '{function_name}' должна быть async")

        self._handlers[module_name] = handler
        return handler
