from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite


class ContextStore:
    """Хранилище контекста системы для LLM с историей событий."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    async def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS context_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT,
                    timestamp TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    full_payload TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_context_task_id ON context_entries(task_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_context_timestamp ON context_entries(timestamp)"
            )
            await db.commit()

    async def store_event(
        self,
        task_id: str | None,
        channel: str,
        message: str,
        payload: dict | None = None,
    ) -> None:
        """Сохранить событие для контекста LLM."""
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO context_entries (task_id, timestamp, channel, summary, full_payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    now,
                    channel,
                    message,
                    json.dumps(payload, ensure_ascii=False) if payload else None,
                    now,
                ),
            )
            await db.commit()

    async def get_recent_context(self, limit: int = 20) -> str:
        """Получить недавние события в формате для LLM промпта."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT timestamp, channel, summary, task_id
                FROM context_entries
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()

        if not rows:
            return "История событий пуста."

        lines = []
        for row in reversed(rows):
            task_part = f" [task: {row['task_id']}]" if row["task_id"] else ""
            lines.append(f"[{row['channel'].upper()}] {row['summary']}{task_part}")

        return "\n".join(lines)

    async def get_task_context(self, task_id: str) -> str:
        """Получить контекст по конкретной задаче."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT timestamp, channel, summary
                FROM context_entries
                WHERE task_id = ?
                ORDER BY id ASC
                """,
                (task_id,),
            )
            rows = await cursor.fetchall()

        if not rows:
            return f"Нет событий для задачи {task_id}."

        lines = [f"Событий для задачи: {len(rows)}"]
        for row in rows:
            lines.append(f"[{row['channel'].upper()}] {row['summary']}")

        return "\n".join(lines)

    async def clear_old(self, days: int = 7) -> None:
        """Удалить старые записи."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "DELETE FROM context_entries WHERE created_at < ?",
                (cutoff,),
            )
            await db.commit()
