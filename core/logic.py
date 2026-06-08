from __future__ import annotations

from contracts.messages import Plan
from core.event_log import EventLog
from core.json_utils import extract_json
from core.lm_studio import LMStudioClient
from core.message_bus import MessageBus
from core.plan_resolver import PlanResolver
from core.planning import ExecutionBundle
from core.recon import build_recon_steps, needs_recon
from core.registry import ModuleRegistry
from core.semantic import SemanticPlan, parse_semantic_from_text, semantic_plan_from_llm_json


class LogicCore:
    PLANNING_MAX_TOKENS = 512

    def __init__(
        self,
        lm_client: LMStudioClient,
        logic_model: str,
        registry: ModuleRegistry,
        bus: MessageBus,
        event_log: EventLog,
    ) -> None:
        self._lm_client = lm_client
        self._logic_model = logic_model
        self._registry = registry
        self._bus = bus
        self._event_log = event_log
        self._resolver = PlanResolver()

    async def build_execution_bundle(self, user_request: str) -> ExecutionBundle:
        await self._event_log.log(
            channel="task",
            source="logic",
            message="Получена задача",
            payload={"request": user_request},
        )

        semantic = await self._get_semantic_plan(user_request)
        bundle = ExecutionBundle(
            goal=user_request,
            description=semantic.description or user_request,
        )

        needs_recon_phase = False
        step_id = 1

        for action in semantic.actions:
            if needs_recon(action, user_request):
                bundle.deferred_actions.append(action)
                needs_recon_phase = True
            else:
                step = self._resolver.resolve_action(step_id, action)
                bundle.ready_steps.append(step)
                step_id += 1

        if needs_recon_phase:
            bundle.recon_steps = build_recon_steps(start_id=step_id)
            await self._event_log.log(
                channel="task",
                source="logic",
                message="Запланирована разведка через file_ops",
                task_id=bundle.task_id,
                payload={
                    "deferred_actions": len(bundle.deferred_actions),
                    "ready_steps": len(bundle.ready_steps),
                },
            )
        else:
            await self._event_log.log(
                channel="task",
                source="logic",
                message="План сформирован без разведки",
                task_id=bundle.task_id,
                payload={"steps": [step.model_dump() for step in bundle.ready_steps]},
            )

        return bundle

    async def build_plan(self, user_request: str) -> Plan:
        bundle = await self.build_execution_bundle(user_request)
        if bundle.deferred_actions:
            raise ValueError(
                "План требует разведки — используйте executor.run вместо build_plan"
            )
        return Plan(
            task_id=bundle.task_id,
            description=bundle.description,
            steps=bundle.ready_steps,
        )

    async def _get_semantic_plan(self, user_request: str) -> SemanticPlan:
        try:
            semantic = await self._build_semantic_with_llm(user_request)
            if semantic.actions:
                return semantic
        except Exception as exc:
            await self._event_log.log(
                channel="error",
                source="logic",
                message=f"LLM-планирование не удалось, fallback на семантический парсер: {exc}",
            )

        semantic = parse_semantic_from_text(user_request)
        if semantic and semantic.actions:
            return semantic

        raise ValueError("Не удалось построить план для задачи")

    async def _build_semantic_with_llm(self, user_request: str) -> SemanticPlan:
        user_prompt = f"""Режим: ПЛАНИРОВАНИЕ

Доступные модули и операции:
{self._registry.describe_for_logic()}

Запрос пользователя:
{user_request}

Если путь неоднозначен — укажи location.path как фразу пользователя, не угадывай абсолютный путь.
Верни JSON с полями description и actions."""

        content = await self._call_logic_json(user_prompt, task_label="planning")
        data = extract_json(content)
        return semantic_plan_from_llm_json(data)

    async def _call_logic_json(self, user_prompt: str, *, task_label: str) -> str:
        try:
            content = await self._lm_client.chat_completion(
                model=self._logic_model,
                messages=[{"role": "user", "content": user_prompt}],
                temperature=0.1,
                max_tokens=self.PLANNING_MAX_TOKENS,
                combine_reasoning=False,
                json_mode=True,
            )
        except Exception:
            content = await self._lm_client.chat_completion(
                model=self._logic_model,
                messages=[{"role": "user", "content": user_prompt}],
                temperature=0.1,
                max_tokens=self.PLANNING_MAX_TOKENS,
                combine_reasoning=True,
                json_mode=False,
            )

        try:
            extract_json(content)
        except ValueError as exc:
            await self._event_log.log(
                channel="error",
                source="logic",
                message=f"{task_label}: {exc}",
                payload={"raw_response": content[:2000]},
            )
            raise

        return content
