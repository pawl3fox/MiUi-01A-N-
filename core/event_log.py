from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from contracts.messages import EventRecord


class EventLog:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    async def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    source TEXT NOT NULL,
                    message TEXT NOT NULL,
                    task_id TEXT,
                    payload TEXT
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_task_id ON events(task_id)"
            )
            await db.commit()

    async def emit(self, event: EventRecord) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO events (timestamp, channel, source, message, task_id, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.timestamp.isoformat(),
                    event.channel,
                    event.source,
                    event.message,
                    event.task_id,
                    json.dumps(event.payload, ensure_ascii=False) if event.payload else None,
                ),
            )
            await db.commit()

    async def log(
        self,
        channel: str,
        source: str,
        message: str,
        task_id: str | None = None,
        payload: dict | None = None,
    ) -> None:
        await self.emit(
            EventRecord(
                channel=channel,
                source=source,
                message=message,
                task_id=task_id,
                payload=payload,
            )
        )

    async def get_events_for_date(self, date: datetime) -> list[EventRecord]:
        start = date.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(hour=23, minute=59, second=59, microsecond=999999)
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT timestamp, channel, source, message, task_id, payload
                FROM events
                WHERE timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp ASC
                """,
                (start.isoformat(), end.isoformat()),
            )
            rows = await cursor.fetchall()
        return [_row_to_event(row) for row in rows]

    async def get_events_by_task(self, task_id: str) -> list[EventRecord]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT timestamp, channel, source, message, task_id, payload
                FROM events
                WHERE task_id = ?
                ORDER BY timestamp ASC
                """,
                (task_id,),
            )
            rows = await cursor.fetchall()
        return [_row_to_event(row) for row in rows]

    async def get_recent(self, limit: int = 50) -> list[EventRecord]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT timestamp, channel, source, message, task_id, payload
                FROM events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
        events = [_row_to_event(row) for row in rows]
        events.reverse()
        return events


def _row_to_event(row: aiosqlite.Row) -> EventRecord:
    payload_raw = row["payload"]
    return EventRecord(
        timestamp=datetime.fromisoformat(row["timestamp"]),
        channel=row["channel"],
        source=row["source"],
        message=row["message"],
        task_id=row["task_id"],
        payload=json.loads(payload_raw) if payload_raw else None,
    )
