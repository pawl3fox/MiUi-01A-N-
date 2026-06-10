# Архитектура Operator v2 — LLM-centric

## Обзор

Система переработана в единую архитектуру с LLM в центре:

```
┌─────────────┐
│   User      │
└──────┬──────┘
       │
       ▼
┌──────────────────────────┐
│   LLMInterface (Qwen)    │  ◄─── Единая точка входа, всегда в памяти
│   - Обработка запроса    │
│   - Парсинг [LOGIC_REQ]  │
│   - Контекст системы     │
└──────┬───────────────────┘
       │
       │ [LOGIC_REQUEST] when needed
       ▼
┌──────────────────────────┐
│  LogicCore (DeepSeek)    │  ◄─── Загружается по запросу LLM
│  - Планирование          │
│  - Выполнение            │
│  - Recovery              │
└──────────────────────────┘
```

## Компоненты

### core/llm_interface.py
- **LLMInterface** — главный интерфейс
- `process_user_message()` — обработка запроса с контекстом
- `format_task_result()` — форматирование результатов
- Парсинг `[LOGIC_REQUEST: ...]` из ответа LLM

### core/context_store.py
- **ContextStore** — хранилище событий системы
- Предоставляет контекст LLM (какие события произошли)
- `get_recent_context()` — получить 15-20 последних событий

### core/orchestrator.py
- Управление переключением моделей
- LLM остаётся в памяти
- Logic загружается/выгружается по требованию

### core/app.py (refactored)
- `handle_message()` — единая точка входа
- `_execute_logic_request()` — обработка запросов Logic
- Интеграция LLMInterface и ContextStore

## Использование

### CLI

```bash
> Создай файл report.txt
Выполняю задачу...
[output from LLM about completion]

> /do Удали старые логи
[forced execution mode]

> /status
[показывает 15 последних событий]

> /context
[показывает контекст LLM]

> /exit
```

### Python API

```python
from core.app import OperatorApp

app = OperatorApp()
await app.startup()

# Любой запрос идёт через LLM
response = await app.handle_message("Создай папку backup")

await app.shutdown()
```

## Механика

### Сценарий 1: Действие

```
User: "Создай файл config.json"
    ↓
LLM: "Я вижу, нужно создать файл. [LOGIC_REQUEST: Создать файл config.json]"
    ↓
LLMInterface парсит [LOGIC_REQUEST]
    ↓
Orchestrator: activate_logic() → загрузить DeepSeek
    ↓
LogicCore: построить план, выполнить
    ↓
Результат → LLM
    ↓
LLM форматирует: "Файл успешно создан."
    ↓
User видит ответ
```

### Сценарий 2: Вопрос

```
User: "Что последнее произошло?"
    ↓
LLM: "Вижу контекст: [последние события из БД]"
    ↓
LLM: "Последний запрос выполнился успешно..."
    ↓
User видит ответ
```

## Отличия от старой версии

| Старая | Новая |
|--------|-------|
| Classifier → решает действие или чат | LLM сама решает через system prompt |
| Жёсткое переключение Logic/LLM | Гибкое, по запросу LLM |
| LLM загружается/выгружается часто | LLM всегда в памяти |
| Нет контекста между запросами | ContextStore хранит историю |
| 2 отдельных запроса к LLM | 1 запрос с контекстом |

## Оптимизация памяти

- **LLM (Qwen 3.5-9B)** — всегда в памяти (~6GB)
- **Logic (DeepSeek R1)** — загружается по запросу (~8GB)
- Переключение между моделями занимает ~2-5 сек

Когда будет больше памяти:
- Обе модели одновременно
- Logic может вызываться асинхронно
- Параллельная обработка

## Логирование и отладка

```bash
> /status
[TASK] task_id: Создание файла...
[INFO] orchestrator: Переключение на Logic (Processor)
[TASK] executor: Отправлен шаг file_ops.create
[INFO] orchestrator: Активна модель LLM (Persona)
```

## Интеграция с существующим кодом

- **Logic ядро** — без изменений
- **Модули и операции** — совместимы
- **Recovery механика** — работает как раньше
- **Message Bus** — тот же

Только входная точка изменилась: теперь это LLM, а не Classifier + Logic выбор.
