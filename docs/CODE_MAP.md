# CODE MAP — короткий маршрутизатор Codex

**Не читайте проект подряд.** Для большинства задач сначала выполните:

```powershell
python scripts/codex_scope.py "описание задачи"
```

Запуск: `video_downloader.py` → `src/bootstrap.py` → `src/application.py`.
`application.py` только собирает mixin-класс; состояние: `src/app/state.py`, пути: `src/app/paths.py`.

## Owner по симптому

| Задача | Читать сначала | Затем при необходимости |
|---|---|---|
| Скачивание не стартует | `downloads/manager.py`, `task.py` | `progress.py`, `core/session_settings.py` |
| yt-dlp lifecycle/fallback | `downloads/youtube_downloader.py` | `youtube_attempt.py`, `youtube_success.py`, `youtube_finalize.py` |
| Ошибки 403/cookies/googlevideo/retry | `downloads/youtube_retry.py` | `youtube_strategy.py`, `youtube_downloader.py` |
| `Conversion failed`/FFmpeg merge/timestamps | `downloads/youtube_failure_diagnostics.py`, `youtube_recovery.py` | `youtube_failure_reporting.py`, `youtube_command.py`, `core/subprocess_runner.py` |
| 1080p/format selector | `downloads/youtube_command.py` | `youtube_downloader.py`, `media_probe.py` |
| MKV/WebM → обязательный MP4 | `downloads/mp4_output.py`, `downloads/files.py` | `youtube_command.py`, `media_probe.py` |
| Порядок clients/chunk/retry profiles | `downloads/youtube_strategy_catalog.py` | `youtube_strategy.py` |
| Proxy/EJS | `downloads/youtube_access.py` | `youtube_command.py` |
| yt-dlp вернул 0, файл не найден | `downloads/ytdlp_results.py` | `files.py` |
| Зависший процесс/timeout/cancel | `core/subprocess_runner.py` | `ytdlp_runtime.py`, `process_control.py` |
| Обновления EXE / GitHub Releases / SHA-256 | `core/app_updates.py`, `ui/update_controls.py` | `tests/test_app_updates.py`, [RELEASE_PROCESS.md](RELEASE_PROCESS.md) |
| Packaging / Actions / release notes | `.github/workflows/python-check.yml`, `RELEASE_NOTES_RU.md` | [RELEASE_PROCESS.md](RELEASE_PROCESS.md), `CHANGELOG.md` |
| Пользовательские ошибки, место логов | `app/paths.py`, `diagnostics/lifecycle.py` | [TROUBLESHOOTING.md](TROUBLESHOOTING.md), `problem_log_validator.py` |
| Настройки | `core/settings.py` | `core/session_settings.py`, `ui/settings_controls.py` |
| MP3 | `audio/convert.py`, `merge.py`, `split.py` | `downloads/media_validation.py` |
| UI | нужный `ui/*.py` | `ui/layout.py` |
| Уведомление о завершении / блокировка папки | `ui/completion_notification.py`, `downloads/progress.py` | `downloads/manager.py`, `ui/settings_controls.py`, `tests/test_completion_notification.py` |
| Логи проблем | `diagnostics/reporting.py`, `events.py` | `snapshots.py` |
| Retention | `diagnostics/retention.py` | `retention_*` |
| Validator | `diagnostics/validator.py` | `validator_*` |

## Контракт yt-dlp

- `VIDEO_MAX_HEIGHT = 1080`, `VIDEO_TARGET_HEIGHT = 1080` — `core/constants.py`.
- lower-quality DASH сохраняется как резерв, поиск 1080p продолжается.
- normal 1080p сначала выбирает merge-safe direct HTTP(S), risky any/HLS оставлен поздним fallback.
- progressive используется как fallback и не требует отдельного A/V merge.
- итоговый опубликованный видеофайл обязан быть `.mp4`; сначала stream-copy remux, перекодирование H.264/AAC только если remux невозможен.
- `youtube_downloader.py` не должен снова накапливать каталоги ошибок, CLI args или финальную diagnostic payload.

## Импортные правила

- `src/core/runtime.py` — compatibility facade для старого API; новый код его не импортирует.
- Константы → `src/core/constants.py`.
- Исключения → `src/core/errors.py`.
- Session setting → `src/core/session_settings.py`.
- Thread-safe контейнеры → `src/core/concurrency.py`.
- Pure UI helpers → `src/ui/helpers.py`.
- Wildcard imports запрещены.

## Проверка

```powershell
python scripts/check_codex_efficiency.py
python -m unittest discover -s tests
```

Точечные тесты указаны самим `scripts/codex_scope.py`.
Полный индекс методов генерируется: `python scripts/generate_code_index.py`.

## Если неясно, что менять

- **Не работает отдельный URL:** сначала `youtube_downloader.py` + соответствующий `youtube_*.py` owner, затем диагностическая сессия и узкий тест; не просматривайте весь каталог.
- **Windows EXE не обновляется:** различайте `core/app_updates.py` (обнаружение и проверка SHA-256) и `ui/update_controls.py` (UI, потоки, согласие пользователя); схема в [RELEASE_PROCESS.md](RELEASE_PROCESS.md).
- **Не создаётся MP3 / ломается объединение:** `audio/convert.py`, `merge.py`, `split.py` и `downloads/media_validation.py`; не удаляйте промежуточные каталоги пользователя автоматически.
- **Ошибка диагностики:** сначала `diagnostics/` и `problem_log_validator.py`, затем [TROUBLESHOOTING.md](TROUBLESHOOTING.md); не записывайте в issue приватные URLs/секреты.

После изменения Python обновляйте [CHANGELOG.md](../CHANGELOG.md). `CODE_INDEX.md` перегенерируйте скриптом, а не вручную. Общая последовательность и границы проверок — в [CODEX_WORKFLOW.md](CODEX_WORKFLOW.md).
