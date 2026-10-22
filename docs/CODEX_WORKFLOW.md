# Codex workflow — от симптома до проверенного diff

[← Документация](README.md) · [Карта кода](CODE_MAP.md) · [Архитектура](ARCHITECTURE.md) · [Релизы и CI](RELEASE_PROCESS.md)

Работайте по циклу **INSPECT → DIAGNOSE → PLAN → CHANGE → VERIFY → REVIEW → DELIVER**. Цель — минимальное изменение правильного owner-модуля, а не переписывание всего приложения.

## Быстрый вход (экономия контекста)

1. Из корня репозитория выполните `python scripts/codex_scope.py "симптом или задача"`.
2. Прочитайте найденный `PRIMARY`, затем лишь нужный `RELATED` и релевантный тест. Если маршрут не найден — [CODE_MAP.md](CODE_MAP.md) и точечный `rg -n "..." src tests`.
3. [CODE_INDEX.md](CODE_INDEX.md) открывайте **только** для поиска имени метода/строки. Он генерируется `python scripts/generate_code_index.py`; не редактируйте его вручную.
4. Перед вмешательством сформулируйте root cause, существующий контракт, риск регрессии и критерии проверки.

## Для багфикса / рефакторинга

1. Соберите воспроизведение: traceback, сообщение UI, лог сессии или падающий тест. Сопоставьте с owner-файлом.
2. Сначала изолированный regression test (когда это применимо), затем **минимальный diff**. Не склеивайте fix с несвязанным рефакторингом.
3. Не скрывайте провалы `except: pass`, отключением проверок или `continue-on-error`. Классифицируйте причину: код, внешняя сеть, инструменты, файл или среда.
4. Для пользовательского поведения сохраняйте существующие контракты: видео MP4 не выше 1080p, проверка ffprobe, MP3 после успешного видео, корректные статусы/отмена, сохранение настроек и истории.
5. Для изменения кода обновите [CHANGELOG.md](../CHANGELOG.md) и связанную документацию. Указания на версии/релизы берите из текущих файлов и workflow.

## Owner-границы

- `video_downloader.py → src/bootstrap.py → src/application.py` — запуск/композиция, не хранилище новой бизнес-логики.
- `downloads/youtube_downloader.py` — управление одной загрузкой; `youtube_attempt.py` — контекст попытки; `youtube_retry.py` — классификация; `youtube_success.py` — успех/кандидат; `youtube_finalize.py` — финальный резерв/отчёт.
- `youtube_command.py` — CLI/формат; `youtube_strategy_catalog.py` — каталог стратегий; `youtube_queue_policy.py` — бюджет/временные исключения очереди.
- `core/app_updates.py` — сравнение релизов и безопасная замена EXE; `ui/update_controls.py` — поток/UI и согласие. Детали: [RELEASE_PROCESS.md](RELEASE_PROCESS.md).
- `app/paths.py` — пути; `diagnostics/*` — проблемы, события, manifest/retention; `ui/*` — Tkinter; `audio/*` — FFmpeg-аудио.

**Не делать:** `from ... import *`, новые импорты `src.core.runtime`, чтение всего `src/` «на всякий случай», перенос стратегий/ошибок yt-dlp в orchestration.

## Проверки по уровням

Из корня репозитория с установленными Python-зависимостями:

```powershell
python -m compileall -q video_downloader.py problem_log_validator.py src scripts tests
python scripts/check_codex_efficiency.py
python -m unittest discover -s tests -v
```

Сначала можно запустить **узкий** тест из маршрутизатора `codex_scope.py`. После изменения диагностики полезно выполнить `python problem_log_validator.py --examples`. Падающий тест требует разбора; если среда не содержит FFmpeg/JS runtime — это нужно указать отдельно, а не выдать за успешную проверку.

Offline-тесты и `--self-test` не подтверждают внешний вид Tkinter, реальную загрузку YouTube или безопасную замену EXE на Windows. Для затронутой области отдельно нужны runtime/Windows/manual checks; если не проведены — **NOT VERIFIED**.

## GitHub / Actions / завершение

- Перед записью в GitHub: проверить ветку, текущий SHA файла, workflows и триггеры; избегать частых пушей.
- Коммит `[package]` запускает сборку и release; вместе с ним нужны актуальные `RELEASE_NOTES_RU.md`. Для обычных docs/code-коммитов **не** используйте `[package]` случайно.
- После изменения workflow проверьте первый run и его failed step/logs до следующих коммитов.
- В отчёте укажите: точные файлы, причину, поведение до/после, реально запущенные проверки, состояние Actions, непроверенные области и потенциальные ограничения.
