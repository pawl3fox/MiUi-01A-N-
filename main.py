from __future__ import annotations



import asyncio

import sys



from core.app import OperatorApp



ACTION_PREFIX = "/do"





async def run_cli() -> None:

    app = OperatorApp()

    try:

        await app.startup()

    except Exception as exc:

        print("Не удалось запустить Operator.")

        print("Проверьте, что LM Studio запущен на localhost:1234 и модели доступны.")

        print(f"Ошибка: {exc}")

        return



    print("Operator MVP")

    print("Команды:")

    print("  <текст>       — чат или действие (классификатор решает сам)")

    print("  /do <задача>  — принудительно выполнить как действие")

    print("  /status       — последние события системы")

    print("  /exit         — выход")

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



            try:

                if text.startswith(ACTION_PREFIX):

                    action_text = text[len(ACTION_PREFIX) :].strip()

                    if not action_text:

                        print("Укажите задачу после /do")

                        continue

                    print("Выполняю задачу...")

                    response = await app.handle_message(action_text, force_action=True)

                else:

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

    events = await app.event_log.get_recent(limit=15)

    if not events:

        print("Событий пока нет.")

        return

    for event in events:

        task_part = f" [{event.task_id}]" if event.task_id else ""

        print(

            f"{event.timestamp.isoformat()} "

            f"[{event.channel}] {event.source}{task_part}: {event.message}"

        )





def main() -> None:

    if sys.platform == "win32":

        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(run_cli())





if __name__ == "__main__":

    main()

