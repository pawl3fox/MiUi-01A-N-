from __future__ import annotations

import json
from typing import Any

from contracts.execution import LogicAction, LogicDecision, StepExecution
from contracts.messages import PlanStep
from core.event_log import EventLog
from core.json_utils import extract_json
from core.lm_studio import LMStudioClient
from core.registry import ModuleRegistry


class RecoveryAdvisor:
    RECOVERY_MAX_TOKENS = 2048

    def __init__(
        self,
        lm_client: LMStudioClient,
        logic_model: str,
        registry: ModuleRegistry,
        event_log: EventLog,
    ) -> None:
        self._lm_client = lm_client
        self._logic_model = logic_model
        self._registry = registry
        self._event_log = event_log

    async def decide(
        self,
        *,
        goal: str,
        task_id: str,
        completed: list[StepExecution],
        failed: StepExecution | None,
        recovery_attempt: int,
        plan_remaining: list[PlanStep],
    ) -> LogicDecision:
        context = self._build_context(
            goal=goal,
            completed=completed,
            failed=failed,
            recovery_attempt=recovery_attempt,
            plan_remaining=plan_remaining,
        )

        user_prompt = f"""Режим: RECOVERY

Доступные модули:
{self._registry.describe_for_logic()}

Контекст выполнения:
{context}

Верни JSON с полями analysis, action, step, note (см. system prompt)."""

        try:
            content = await self._lm_client.chat_completion(
                model=self._logic_model,
                messages=[{"role": "user", "content": user_prompt}],
                temperature=0.15,
                max_tokens=self.RECOVERY_MAX_TOKENS,
                combine_reasoning=True,
                json_mode=True,
            )
        except Exception:
            content = await self._lm_client.chat_completion(
                model=self._logic_model,
                messages=[{"role": "user", "content": user_prompt}],
                temperature=0.15,
                max_tokens=self.RECOVERY_MAX_TOKENS,
                combine_reasoning=True,
                json_mode=False,
            )

        try:
            data = extract_json(content)
        except ValueError as exc:
            await self._event_log.log(
                channel="error",
                source="recovery",
                message=str(exc),
                task_id=task_id,
                payload={"raw_response": content[:2000]},
            )
            raise

        decision = _parse_decision(data)

        await self._event_log.log(
            channel="task",
            source="recovery",
            message=f"Решение: {decision.action.value}",
            task_id=task_id,
            payload={
                "analysis": decision.analysis,
                "action": decision.action.value,
                "step": decision.step.model_dump() if decision.step else None,
                "note": decision.note,
                "attempt": recovery_attempt,
            },
        )
        return decision

    def _build_context(
        self,
        *,
        goal: str,
        completed: list[StepExecution],
        failed: StepExecution | None,
        recovery_attempt: int,
        plan_remaining: list[PlanStep],
    ) -> str:
        completed_brief = [
            {
                "module": item.module,
                "operation": item.operation,
                "success": item.success,
                "result": item.result,
                "error": item.error,
                "source": item.source,
            }
            for item in completed[-8:]
        ]
        payload: dict[str, Any] = {
            "goal": goal,
            "recovery_attempt": recovery_attempt,
            "completed_steps": completed_brief,
            "plan_remaining": [step.model_dump() for step in plan_remaining],
        }
        if failed:
            payload["failed_step"] = {
                "module": failed.module,
                "operation": failed.operation,
                "payload": failed.payload,
                "error": failed.error,
                "attempt": failed.attempt,
            }
        return json.dumps(payload, ensure_ascii=False, indent=2)


def _parse_decision(data: dict[str, Any]) -> LogicDecision:
    action_raw = str(data.get("action", "abort")).lower()
    action_map = {
        "abort": LogicAction.ABORT,
        "complete": LogicAction.COMPLETE,
        "execute_step": LogicAction.EXECUTE_STEP,
        "resume_plan": LogicAction.RESUME_PLAN,
    }
    action = action_map.get(action_raw, LogicAction.ABORT)

    step_data = data.get("step")
    step = PlanStep.model_validate(step_data) if step_data else None

    return LogicDecision(
        analysis=str(data.get("analysis", "")),
        action=action,
        step=step,
        note=str(data.get("note", "")),
    )
