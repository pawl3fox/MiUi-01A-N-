from __future__ import annotations

import json
import re
from dataclasses import dataclass

from core.context_store import ContextStore
from core.event_log import EventLog
from core.lm_studio import LMStudioClient


@dataclass
class LogicRequest:
    """Запрос к Logic ядру от LLM."""

    task_description: str
    context_summary: str | None = None


@dataclass
class LLMResponse:
    """Ответ от LLM."""

    text: str
    logic_request: LogicRequest | None = None


class LLMInterface:
    """Интерфейс взаимодействия с LLM как единой точкой входа."""

    def __init__(
        self,
        lm_client: LMStudioClient,
        llm_model: str,
        event_log: EventLog,
        context_store: ContextStore,
    ) -> None:
        self._lm_client = lm_client
        self._llm_model = llm_model
        self._event_log = event_log
        self._context_store = context_store

    async def process_user_message(
        self,
        user_message: str,
        system_prompt: str | None = None,
    ) -> LLMResponse:
        """Обработать сообщение пользователя через LLM.

        LLM сама решает:
        - Нужно ли вызывать Logic
        - Нужна ли информация из системного контекста
        - Как сформулировать ответ

        Returns:
            LLMResponse с текстом ответа и опциональным запросом к Logic.
        """
        recent_context = await self._context_store.get_recent_context(limit=15)

        if system_prompt is None:
            system_prompt = self._get_default_system_prompt()

        full_prompt = f"""{system_prompt}

Текущий контекст системы:
{recent_context}

Запрос пользователя: {user_message}"""

        try:
            response = await self._lm_client.chat_completion(
                model=self._llm_model,
                messages=[{"role": "user", "content": full_prompt}],
                temperature=0.7,
                max_tokens=1024,
            )
        except Exception as exc:
            await self._event_log.log(
                channel="error",
                source="llm_interface",
                message=f"Ошибка LLM: {exc}",
            )
            raise

        await self._event_log.log(
            channel="info",
            source="llm_interface",
            message="LLM обработала запрос",
            payload={"user_message": user_message[:100]},
        )

        # Парсить ответ на предмет запроса к Logic
        logic_request = self._extract_logic_request(response, user_message)

        if logic_request:
            await self._event_log.log(
                channel="info",
                source="llm_interface",
                message="LLM запросила выполнение задачи в Logic",
                payload={"task_description": logic_request.task_description},
            )

        return LLMResponse(
            text=response.strip(),
            logic_request=logic_request,
        )

    def _extract_logic_request(
        self,
        response: str,
        original_request: str,
    ) -> LogicRequest | None:
        """Извлечь запрос к Logic из ответа LLM.

        Ищет паттерн [LOGIC_REQUEST: ...] в ответе.
        """
        match = re.search(r"\[LOGIC_REQUEST:\s*(.+?)\]", response, re.DOTALL)
        if not match:
            return None

        try:
            request_text = match.group(1).strip()
            # Попытаться парсить как JSON если содержит '{'
            if request_text.startswith("{"):
                data = json.loads(request_text)
                task_desc = data.get("task", request_text)
            else:
                task_desc = request_text

            return LogicRequest(
                task_description=task_desc,
                context_summary=original_request,
            )
        except (json.JSONDecodeError, ValueError):
            return None

    async def format_task_result(
        self,
        user_request: str,
        task_status: str,
        task_summary: str,
    ) -> str:
        """Сформировать ответ пользователю на основе результата задачи.

        Args:
            user_request: Исходный запрос пользователя
            task_status: Статус задачи (completed, failed)
            task_summary: Краткий summary результата

        Returns:
            Отформатированный ответ от LLM
        """
        prompt = f"""Режим: response_to_task_result

Исходный запрос: {user_request}
Статус выполнения: {task_status}
Результат: {task_summary}

Дай краткий, естественный ответ пользователю. 1-3 предложения, только факты."""

        try:
            response = await self._lm_client.chat_completion(
                model=self._llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=256,
            )
            return response.strip()
        except Exception as exc:
            await self._event_log.log(
                channel="error",
                source="llm_interface",
                message=f"Ошибка форматирования результата: {exc}",
            )
            # Fallback ответ
            if task_status == "completed":
                return f"Задача выполнена. Результат: {task_summary}"
            else:
                return f"Не удалось выполнить задачу: {task_summary}"

    def _get_default_system_prompt(self) -> str:
        """Системный prompt для LLM."""
        return """Ты — Operator, локальный ассистент на русском языке.

Общайся естественно и по делу.

Когда пользователь просит что-то сделать в системе (создать файл, открыть папку и т.д.):
- Используй формат [LOGIC_REQUEST: <описание задачи>] для отправки задачи в Logic ядро
- Жди результата и потом расскажи пользователю что произошло

Когда пользователь задаёт вопрос:
- Отвечай на основе доступного контекста
- Если нужна информация из системы — указывай это в ответе

Примеры:
- "Создай файл todo.txt" → [LOGIC_REQUEST: Создать текстовый файл todo.txt в стандартной папке документов]
- "Что было последнее?" → Обратись к контексту выше и расскажи
"""
