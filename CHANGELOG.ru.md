# Журнал изменений

[English](CHANGELOG.md) | **Русский**

## v0.5.0 - 2026-08-03

- Усилено принудительное применение scope для агентов и MCP, сохранены монотонные watermarks синхронизации/backfill и возобновляемое восстановление `FLOOD_WAIT`.
- Добавлено транзакционное сопровождение архива через схему v6, диагностика `doctor`, фильтрованная инспекция jobs, явный retry и previews ремонта очереди.
- Добавлены маршрутизация контекста с бюджетами и guidance для Codex Spark/Luna, неизменяемые наборы evidence с цитатами, research sessions и локальная для профиля wiki memory.
- Добавлен опциональный полностью локальный гибридный поиск с явной конфигурацией embeddings, свежестью vector, детерминированными evidence с цитатами и keyword fallback.
- Добавлены ограниченные приватные AI export packs с проверяемыми manifests; архивы, сессии, медиафайлы, wiki, экспорты и packs остаются приватными по умолчанию.
- Добавлен opt-in синтез OpenAI Responses с проверками политики и локальным cited fallback, когда провайдер отключён или недоступен.
- Расширены совместимый CLI и read-only MCP surface при сохранении local-first границ приватности и JSON contracts.

## v0.2.0 - 2026-08-03

- Публичные CLI и пакет переименованы в `tg-recall`; сохранены устаревшие алиасы исполняемых файлов.
- Добавлены platform-aware roots, portable `--home` mode и изолированные профили Telegram.
- Новые медиафайлы сохраняются по относительным content-addressed keys вместо абсолютных путей файловой системы.
- Добавлены политики scope для media/transcription, `sync ensure`, обнаружение локального Whisper, команды agent guide/retrieve/export/materialize, безопасная legacy migration и согласованные backups.

## v0.1.0 - 2026-08-03

Первый публичный early-alpha релиз.

- Локальный архив SQLite, авторизация Telegram MTProto, scoped sync, очередь медиа, хранение транскриптов, FTS search и retrieval с цитатами.
- Поверхность операций CLI и локальный MCP server только для чтения.
- Усиление приватности на уровне пакета: строгий allowlist source distribution и тесты, отклоняющие непубличное содержимое релиза.

Известные ограничения документированы в [README.ru.md](README.ru.md).
