from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TypeVar

import aiosqlite
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

QUEUE_TASKS = "tasks"
QUEUE_RESULTS = "results"


class MessageBus(ABC):
    @abstractmethod
    async def initialize(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def publish(self, queue_name: str, message: BaseModel) -> str:
        raise NotImplementedError

    @abstractmethod
    async def consume(self, queue_name: str) -> tuple[str, dict] | None:
        raise NotImplementedError

    @abstractmethod
    async def ack(self, message_id: str) -> None:
        raise NotImplementedError


class SQLiteMessageBus(MessageBus):
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    async def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS queue_messages (
                    id TEXT PRIMARY KEY,
                    queue_name TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    claimed_at TEXT
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_queue_pending ON queue_messages(queue_name, status)"
            )
            await db.commit()

    async def publish(self, queue_name: str, message: BaseModel) -> str:
        from datetime import datetime, timezone
        from uuid import uuid4

        message_id = str(uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        payload = message.model_dump_json()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO queue_messages (id, queue_name, payload, status, created_at)
                VALUES (?, ?, ?, 'pending', ?)
                """,
                (message_id, queue_name, payload, created_at),
            )
            await db.commit()
        return message_id

    async def consume(self, queue_name: str) -> tuple[str, dict] | None:
        from datetime import datetime, timezone

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT id, payload
                FROM queue_messages
                WHERE queue_name = ? AND status = 'pending'
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (queue_name,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            claimed_at = datetime.now(timezone.utc).isoformat()
            await db.execute(
                """
                UPDATE queue_messages
                SET status = 'processing', claimed_at = ?
                WHERE id = ?
                """,
                (claimed_at, row["id"]),
            )
            await db.commit()
        return row["id"], json.loads(row["payload"])

    async def ack(self, message_id: str) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE queue_messages SET status = 'done' WHERE id = ?",
                (message_id,),
            )
            await db.commit()
