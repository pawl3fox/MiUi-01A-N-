from __future__ import annotations

import re
from enum import Enum

from core.event_log import EventLog
from core.lm_studio import LMStudioClient


class Intent(str, Enum):
    CHAT = "chat"
    ACTION = "action"


_ACTION_PATTERN = re.compile(
    r"(?:^|\b)("
    r"создай|создать|удали|удалить|запиши|записать|скопируй|копируй|"
    r"перемести|переименуй|сделай|выполни|открой|запусти|"
    r"покажи\s+список|список\s+файлов|mkdir|write|delete|move|copy"
    r")(?:\b|$)",
    re.IGNORECASE,
)

_CHAT_PATTERN = re.compile(
    r"^(?:"
    r"привет|здравствуй|добрый\s+(?:день|вечер|утро)|"
    r"как\s+дела|кто\s+ты|что\s+ты\s+умеешь|спасибо|пока|"
    r"проверка|ответь|поболтаем|хаха|лол"
    r")\b",
    re.IGNORECASE,
)


class IntentClassifier:
    def __init__(
        self,
        lm_client: LMStudioClient,
        llm_model: str,
        event_log: EventLog,
    ) -> None:
        self._lm_client = lm_client
        self._llm_model = llm_model
        self._event_log = event_log

    async def classify(self, text: str) -> Intent:
        cleaned = text.strip()
        if not cleaned:
            return Intent.CHAT

        if _CHAT_PATTERN.search(cleaned) and not _ACTION_PATTERN.search(cleaned):
            intent = Intent.CHAT
            await self._log_intent(cleaned, intent, "rules")
            return intent

        if _ACTION_PATTERN.search(cleaned):
            intent = Intent.ACTION
            await self._log_intent(cleaned, intent, "rules")
            return intent

        intent = await self._classify_with_llm(cleaned)
        await self._log_intent(cleaned, intent, "llm")
        return intent

    async def _classify_with_llm(self, text: str) -> Intent:
        reply = await self._lm_client.chat_completion(
            model=self._llm_model,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Классифицируй запрос пользователя.\n"
                        "Ответь строго одним словом: CHAT или ACTION.\n"
                        "ACTION — если нужно что-то сделать в системе "
                        "(файлы, папки, команды).\n"
                        "CHAT — если это разговор, вопрос, болтовня.\n\n"
                        f"Запрос: {text}"
                    ),
                },
            ],
            temperature=0.0,
            max_tokens=16,
        )
        normalized = reply.strip().upper()
        if "ACTION" in normalized:
            return Intent.ACTION
        return Intent.CHAT

    async def _log_intent(self, text: str, intent: Intent, method: str) -> None:
        await self._event_log.log(
            channel="info",
            source="classifier",
            message=f"Intent: {intent.value} ({method})",
            payload={"text": text, "method": method},
        )
