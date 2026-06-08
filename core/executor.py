from __future__ import annotations

import asyncio

from contracts.execution import LogicAction, StepExecution, TaskRunResult
from contracts.messages import PlanStep, ResultMessage, TaskMessage
from core.event_log import EventLog
from core.logic import LogicCore
from core.lm_studio import LMStudioClient
from core.message_bus import QUEUE_TASKS, MessageBus
from core.plan_resolver import PlanResolver
from core.planning import ExecutionBundle
from core.recon import (
    ReconContext,
    apply_resolved_directory,
    match_directory,
    pick_directory_light,
)
from core.recovery import RecoveryAdvisor
from core.registry import ModuleRegistry


class TaskExecutor:
    def __init__(
        self,
        logic: LogicCore,
        recovery: RecoveryAdvisor,
        registry: ModuleRegistry,
        bus: MessageBus,
        event_log: EventLog,
        lm_client: LMStudioClient,
        logic_model: str,
        results_queue: asyncio.Queue[ResultMessage],
        *,
        task_timeout_seconds: int = 120,
        max_recovery_attempts: int = 8,
        max_total_steps: int = 24,
    ) -> None:
        self._logic = logic
        self._recovery = recovery
        self._registry = registry
        self._bus = bus
        self._event_log = event_log
        self._lm_client = lm_client
        self._logic_model = logic_model
        self._resolver = PlanResolver()
        self._results_queue = results_queue
        self._task_timeout = task_timeout_seconds
        self._max_recovery_attempts = max_recovery_attempts
        self._max_total_steps = max_total_steps
        self._step_counter = 0

    async def run(self, user_request: str) -> TaskRunResult:
        bundle = await self._logic.build_execution_bundle(user_request)
        await self._event_log.log(
            channel="task",
            source="executor",
            message="Начато выполнение задачи",
            task_id=bundle.task_id,
            payload={
                "recon_steps": len(bundle.recon_steps),
                "deferred_actions": len(bundle.deferred_actions),
                "ready_steps": len(bundle.ready_steps),
            },
        )

        completed: list[StepExecution] = []
        recon_context = ReconContext()

        for step in bundle.recon_steps:
            execution = await self._run_step(
                bundle.task_id,
                step,
                source="recon",
            )
            completed.append(execution)
            recon_context.ingest_step(execution)

        action_steps: list[PlanStep] = list(bundle.ready_steps)
        next_id = len(action_steps) + len(completed) + 1

        for action in bundle.deferred_actions:
            directory = match_directory(user_request, action, recon_context)
            if not directory:
                directory = await pick_directory_light(
                    self._lm_client,
                    self._logic_model,
                    user_request,
                    action,
                    recon_context,
                )
            if not directory:
                return TaskRunResult(
                    task_id=bundle.task_id,
                    goal=user_request,
                    status="failed",
                    steps=completed,
                    final_error="Не удалось определить директорию после разведки",
                )

            resolved_action = apply_resolved_directory(action, directory)
            step = self._resolver.resolve_action(next_id, resolved_action)
            action_steps.append(step)
            next_id += 1
            await self._event_log.log(
                channel="task",
                source="executor",
                message="Путь выбран после разведки",
                task_id=bundle.task_id,
                payload={
                    "directory": directory,
                    "operation": step.operation,
                    "path": step.payload.get("path"),
                },
            )

        pending = list(action_steps)
        failed: StepExecution | None = None
        recovery_attempts = 0
        in_recovery = False
        final_analysis: str | None = None

        while len(completed) < self._max_total_steps:
            if not in_recovery and pending:
                step = pending.pop(0)
                execution = await self._run_step(
                    bundle.task_id,
                    step,
                    source="plan",
                )
                completed.append(execution)
                if execution.success:
                    failed = None
                    if not pending:
                        result = TaskRunResult(
                            task_id=bundle.task_id,
                            goal=user_request,
                            status="completed",
                            steps=completed,
                        )
                        await self._log_task_finished(bundle.task_id, result)
                        return result
                    continue
                failed = execution
                in_recovery = True
                await self._event_log.log(
                    channel="error",
                    source="executor",
                    message=f"Шаг провален: {execution.error}",
                    task_id=bundle.task_id,
                    payload=execution.model_dump(),
                )
                continue

            if not in_recovery:
                break

            if recovery_attempts >= self._max_recovery_attempts:
                return TaskRunResult(
                    task_id=bundle.task_id,
                    goal=user_request,
                    status="failed",
                    steps=completed,
                    final_error=failed.error if failed else "Превышен лимит попыток",
                    final_analysis=final_analysis,
                )

            recovery_attempts += 1
            decision = await self._recovery.decide(
                goal=user_request,
                task_id=bundle.task_id,
                completed=completed,
                failed=failed,
                recovery_attempt=recovery_attempts,
                plan_remaining=pending,
            )
            final_analysis = decision.analysis

            if decision.action == LogicAction.ABORT:
                return TaskRunResult(
                    task_id=bundle.task_id,
                    goal=user_request,
                    status="failed",
                    steps=completed,
                    final_error=failed.error if failed else decision.note,
                    final_analysis=decision.analysis,
                )

            if decision.action == LogicAction.COMPLETE:
                result = TaskRunResult(
                    task_id=bundle.task_id,
                    goal=user_request,
                    status="completed",
                    steps=completed,
                    final_analysis=decision.analysis,
                )
                await self._log_task_finished(bundle.task_id, result)
                return result

            if decision.action in {LogicAction.EXECUTE_STEP, LogicAction.RESUME_PLAN}:
                if decision.step is None:
                    continue
                source = (
                    "recovery_resume"
                    if decision.action == LogicAction.RESUME_PLAN
                    else "recovery"
                )
                execution = await self._run_step(
                    bundle.task_id,
                    decision.step,
                    source=source,
                    recovery_analysis=decision.analysis,
                )
                completed.append(execution)
                recon_context.ingest_step(execution)

                if decision.action == LogicAction.RESUME_PLAN:
                    if execution.success:
                        failed = None
                        in_recovery = False
                        continue
                    failed = execution
                    continue

                if execution.success:
                    continue
                failed = execution
                continue

        return TaskRunResult(
            task_id=bundle.task_id,
            goal=user_request,
            status="failed",
            steps=completed,
            final_error=failed.error if failed else "Превышен лимит шагов",
            final_analysis=final_analysis,
        )

    async def _run_step(
        self,
        task_id: str,
        step: PlanStep,
        *,
        source: str,
        recovery_analysis: str | None = None,
    ) -> StepExecution:
        manifest = self._registry.get(step.module)
        if manifest is None:
            return StepExecution(
                step_id=step.step_id,
                module=step.module,
                operation=step.operation,
                payload=step.payload,
                success=False,
                error=f"Модуль '{step.module}' не найден",
                source=source,
                recovery_analysis=recovery_analysis,
            )
        operation = manifest.get_operation(step.operation)
        if operation is None:
            return StepExecution(
                step_id=step.step_id,
                module=step.module,
                operation=step.operation,
                payload=step.payload,
                success=False,
                error=f"Операция '{step.operation}' не найдена",
                source=source,
                recovery_analysis=recovery_analysis,
            )

        self._step_counter += 1
        runtime_step_id = self._step_counter
        task = TaskMessage(
            parent_task_id=task_id,
            step_id=runtime_step_id,
            module=step.module,
            operation=step.operation,
            payload=step.payload,
        )
        await self._bus.publish(QUEUE_TASKS, task)
        await self._event_log.log(
            channel="task",
            source="executor",
            message=f"Отправлен шаг {step.module}.{step.operation}",
            task_id=task_id,
            payload={**task.model_dump(), "source": source},
        )

        result = await self._wait_result(task_id, runtime_step_id)
        return StepExecution(
            step_id=runtime_step_id,
            module=step.module,
            operation=step.operation,
            payload=step.payload,
            success=result.success,
            result=result.result,
            error=result.error,
            source=source,
            recovery_analysis=recovery_analysis,
        )

    async def _log_task_finished(self, task_id: str, result: TaskRunResult) -> None:
        paths = [
            step.result.get("path") or step.result.get("destination")
            for step in result.steps
            if step.success and step.result
        ]
        paths = [path for path in paths if path]
        await self._event_log.log(
            channel="task",
            source="executor",
            message="Задача завершена",
            task_id=task_id,
            payload={
                "status": result.status,
                "paths": paths,
                "steps_count": len(result.steps),
            },
        )

    async def _wait_result(self, task_id: str, step_id: int) -> ResultMessage:
        backlog: list[ResultMessage] = []
        try:
            while True:
                result = await asyncio.wait_for(
                    self._results_queue.get(),
                    timeout=self._task_timeout,
                )
                if result.task_id == task_id and result.step_id == step_id:
                    for item in backlog:
                        await self._results_queue.put(item)
                    return result
                backlog.append(result)
        except asyncio.TimeoutError as exc:
            for item in backlog:
                await self._results_queue.put(item)
            raise TimeoutError(
                f"Таймаут ожидания результата шага {step_id} задачи {task_id}"
            ) from exc
