# Документация ZeTer Video Downloader

[← Главная (RU)](../README.md) · [README in English](../README_EN.md) · [Скачать Windows EXE](https://github.com/zeter1/ZeTer-Video-Downloader/releases)

Эта папка разделена по задачам: **пользователь → устранение проблемы**, **разработчик → конкретный owner-модуль**, **сопровождающий → сборка и выпуск**. Не нужно читать всё подряд.

## Пользователю

| Задача | Куда перейти |
| --- | --- |
| Первое скачивание, интерфейс, MP4 и MP3, HTML-плейлисты | [Руководство в основном README](../README.md#использование) |
| Загрузка не работает, нет MP3, обновление или SmartScreen | [Диагностика и устранение неполадок](TROUBLESHOOTING.md) |
| Как предоставить сведения об ошибке без раскрытия секретов | [Поддержка](../SUPPORT.md) · [Безопасность](../SECURITY.md) |
| Что изменилось в программе | [История изменений](../CHANGELOG.md) |

**Различайте два режима:** готовый Windows x64 `ZeTer-Video-Downloader.exe` содержит свои копии yt-dlp, FFmpeg, ffprobe и Deno; при запуске из исходников зависимости устанавливаются отдельно. Для EXE не требуется установка Python.

## Разработчику и ChatGPT/Codex

| Цель | Документ |
| --- | --- |
| Быстро определить владельца функциональности или ошибки | [CODE_MAP.md](CODE_MAP.md) |
| Сделать минимальный исправляющий diff, не разрушив архитектуру | [CODEX_WORKFLOW.md](CODEX_WORKFLOW.md) |
| Понять контракты качества, очереди, диагностики, UI и обновления | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Найти конкретный символ / метод | [CODE_INDEX.md](CODE_INDEX.md) — **генерируемый**, не редактировать вручную |
| Сборка, тесты, выпуск EXE, хеш и политика Actions | [RELEASE_PROCESS.md](RELEASE_PROCESS.md) |

### Начальная последовательность

```powershell
python scripts/codex_scope.py "симптом или задача"
# Читать PRIMARY + по необходимости RELATED + релевантный тест
python scripts/check_codex_efficiency.py
python -m unittest discover -s tests
```

Запускайте эти команды из корня репозитория и в Python-окружении с зависимостями из `requirements.txt`. Windows EXE и живые загрузки с сайтов требуют отдельных проверок.

## Навигация по исходникам

```text
video_downloader.py → src/bootstrap.py → src/application.py
  src/app/          пути и состояние
  src/core/         настройки, процессы, обновление EXE
  src/downloads/    yt-dlp, URL, очередь, MP4 и ограничения 1080p
  src/audio/        MP3, объединение и разделение
  src/ui/           Tkinter и уведомления
  src/diagnostics/  события, отчёты, валидация, retention
tests/              offline-регрессионные тесты
scripts/            Codex-маршрутизация и индексация
```

**Источники истины:** при расхождении описания и поведения сначала проверьте текущий код, тесты и опубликованный workflow. Исправления документации должны отражать фактические возможности, а не обещать неподтверждённые функции.
