from __future__ import annotations

import asyncio
import sys

from core.app import OperatorApp


async def run_cli() -> None:
    """Запустить CLI интерфейс Operator."""
    app = OperatorApp()

    try:
        await app.startup()
    except Exception as exc:
        print("Не удалось запустить Operator.")
        print("Проверьте, что LM Studio запущен на localhost:1234 и модели доступны.")
        print(f"Ошибка: {exc}")
        return

    print("Operator — Unified LLM-based Agent")
    print("=" * 50)
    print("Команды:")
    print("  <текст>       — естественный ввод (LLM решает сама)")
    print("  /do <задача>  — принудительно выполнить как действие")
    print("  /status       — последние события системы")
    print("  /context      — текущий контекст LLM")
    print("  /exit         — выход")
    print("=" * 50)
    print()

    try:
        while True:
            try:
                user_input = await asyncio.to_thread(input, "> ")
            except (EOFError, KeyboardInterrupt):
                print("\nВыход.")
                break

            text = user_input.strip()
            if not text:
                continue

            if text == "/exit":
                break

            if text == "/status":
                await _print_status(app)
                continue

            if text == "/context":
                await _print_context(app)
                continue

            try:
                if text.startswith("/do "):
                    action_text = text[4:].strip()
                    if not action_text:
                        print("Укажите задачу после /do")
                        continue
                    print("Выполняю действие через Logic...")
                    response = await app.handle_message(action_text, force_action=True)
                else:
                    # LLM сама решает через [LOGIC_REQUEST]
                    response = await app.handle_message(text)

                print(response)

            except Exception as exc:
                print(f"Ошибка: {exc}")
                await app.event_log.log(
                    channel="error",
                    source="cli",
                    message=str(exc),
                )

    finally:
        await app.shutdown()


async def _print_status(app: OperatorApp) -> None:
    """Показать последние события системы."""
    events = await app.event_log.get_recent(limit=15)

    if not events:
        print("Событий пока нет.")
        return

    print("Последние события:")
    for event in events:
        task_part = f" [task: {event.task_id}]" if event.task_id else ""
        print(
            f"{event.timestamp.strftime('%H:%M:%S')} "
            f"[{event.channel.upper():6}] {event.source:12} {task_part}: {event.message}"
        )


async def _print_context(app: OperatorApp) -> None:
    """Показать текущий контекст LLM."""
    context = await app.context_store.get_recent_context(limit=10)
    print("Контекст системы для LLM:")
    print(context)


def main() -> None:
    """Точка входа."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(run_cli())


if __name__ == "__main__":
    main()
