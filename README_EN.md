**Language / Язык:** [Русский](README.md) · **English**

# Universal Video Downloader

[![Python checks](https://github.com/zeter1/Universal-Video-Downloader/actions/workflows/python-check.yml/badge.svg)](https://github.com/zeter1/Universal-Video-Downloader/actions/workflows/python-check.yml)
[![Releases](https://img.shields.io/badge/Windows-EXE%20Releases-blue?logo=github)](https://github.com/zeter1/Universal-Video-Downloader/releases)

**Universal Video Downloader** is a Windows desktop application that downloads videos from sites supported by [yt-dlp](https://github.com/yt-dlp/yt-dlp), produces **MP4 video up to 1080p**, and automatically generates **MP3 audio** from successfully downloaded videos.

The application combines downloading, media processing, URL queue management, output validation and diagnostics in one interface. End users can run the packaged EXE **without separately installing Python, FFmpeg or the other bundled tools**.

**[📥 Download for Windows](https://github.com/zeter1/Universal-Video-Downloader/releases)** · **[📖 Changelog](CHANGELOG.md)** · **[🐞 Report a bug](https://github.com/zeter1/Universal-Video-Downloader/issues/new/choose)**

> **Please note:** published Windows builds are currently marked **Prerelease** and are not commercially code-signed. Website compatibility depends on the site's policies, network access and yt-dlp support.

## Contents

- [Quick start](#quick-start)
- [Features](#features)
- [How to use](#how-to-use)
- [Where files are stored](#where-files-are-stored)
- [Application updates](#application-updates)
- [Verifying the downloaded EXE](#verifying-the-downloaded-exe)
- [Run from source](#run-from-source)
- [Troubleshooting](#troubleshooting)
- [Architecture](#architecture)
- [Testing and packaging](#testing-and-packaging)
- [Limitations and security](#limitations-and-security)
- [Contact](#contact)

## Quick start

**Available build:** Windows x64, a standalone `Universal-Video-Downloader.exe` (no installer).

1. Open **[GitHub Releases](https://github.com/zeter1/Universal-Video-Downloader/releases)**. Since releases are marked *Prerelease*, choose the latest published build from the release list instead of relying on GitHub's `/releases/latest` endpoint.
2. Expand **Assets** and download `Universal-Video-Downloader.exe`. The companion `Universal-Video-Downloader.sha256.txt` is only needed if you want to verify the checksum yourself.
3. Place the EXE in a **writable folder**, for example `Documents\Universal-Video-Downloader`, and run it. Avoid `Program Files`: the app stores its settings and logs next to the executable.
4. Paste one URL per line, choose a destination folder and click **«▶ Скачать все»** (*Download all*).
5. Find the results in the **`Видео`** (Video) and **`Аудио`** (Audio) folders inside the chosen destination.

The PyInstaller Windows build includes **yt-dlp, FFmpeg, ffprobe and Deno**. No Python installation or `requirements.txt` setup is needed for the EXE. An internet connection is required for downloading; some websites require authorization or may be inaccessible on your network.

> If Windows SmartScreen shows an unknown-publisher warning, first confirm that the EXE came from this repository's Releases page and, if needed, verify its SHA-256. A warning can occur because the executable is unsigned; it does not, by itself, prove the file is safe or malicious.

## Features

| Feature | Behavior |
| --- | --- |
| **MP4 video** | Automatically prioritizes 1080p-class quality with a strict maximum of 1080 vertical pixels; falls back to lower available quality when necessary |
| **MP3 audio** | Automatically extracts audio from successfully downloaded videos; selectable 128/192/256/320 kbps or VBR-0 quality |
| **Avoid unnecessary re-encoding** | Prefers compatible MP4/M4A streams and lossless remuxing; H.264/AAC transcoding is a fallback |
| **URL queue** | Multiple URLs, duplicate detection and history-aware skips; up to 500 URLs per session |
| **YouTube HTML playlist import** | Import a saved HTML page, select entries and filter previously downloaded videos |
| **Concurrent downloads** | Configurable from 1 to 2 downloads at once |
| **Merged audio** | Optionally combine successfully generated MP3 files, with duration-based splitting |
| **Additional options** | Subtitles when available, network retries, fallback yt-dlp strategies and completion notifications |
| **Diagnostics** | UI logs, per-session files, links requiring manual attention and structured error reports |
| **App updates** | Background GitHub Release checks and user-approved installation of a verified EXE |

**Important:** the current UI does **not** offer a separate *video-only / MP3-only* selector. A normal session first downloads video and then converts successful downloads to MP3. This documentation describes the current implementation, not a planned feature.

## How to use

### Download one or more videos

1. Copy a video link from a website supported by yt-dlp.
2. Paste one or more URLs into **«Ссылки для скачивания»** (Download links), **one URL per line**, or click **«📋 Вставить из буфера»** (Paste from clipboard).
3. Set **«Путь сохранения»** (Save location). Video quality is automatically managed with a preference for Full HD and a maximum of 1080p; there is no manual video-resolution selector.
4. Optionally choose the MP3 quality, subtitles and number of concurrent downloads.
5. Click **«▶ Скачать все»** (Download all). Follow progress in **«Весь лог загрузки»** (All logs), **«Не скачалось»** (Failed downloads) and **«Ошибки»** (Errors).
6. Wait for both download and MP3 conversion to finish. The app shows a completion notification; **«⏹ Отменить»** (Cancel) stops processing.

Duplicate URLs in a queue are removed; videos recorded as previously downloaded are normally skipped. **«🗂 Очистить лог скачанных»** clears the downloaded-video record, so use it deliberately.

### Import a YouTube HTML playlist

1. Save the playlist page as an HTML file.
2. Click **«📂 Загрузить плейлист из .html»** and select that file.
3. Select videos from the imported list (or **«Только новые»**, *New only*) and click **«Добавить выбранные в очередь»** (*Add selected to queue*).
4. Start the resulting queue with **«Скачать все»**.

Parsing depends on the structure of the saved YouTube page; arbitrary HTML and every possible future page layout are not guaranteed to work.

### Merge MP3 files

Enable **«Объединить все аудио в один файл»** (*Merge all audio into one file*). If needed, set the split duration: **0** means no splitting, up to **10 hours** per part. Only **successfully generated MP3 files from the current session** are merged, even when some downloads fail.

### MP4 processing pipeline

```text
URL → yt-dlp → format selection (prefer 1080p, maximum 1080p)
                        ↓
       compatible streams / separate video + audio
                        ↓
         direct MP4 or lossless remux
                        ↓
         H.264/AAC transcoding only if needed
                        ↓
              ffprobe validation
                        ↓
             Видео/*.mp4 → Аудио/*.mp3
```

The available formats depend on the source website. A video being playable on YouTube does not guarantee a particular resolution or audio track can be downloaded.

## Where files are stored

The **`Видео`** and **`Аудио`** directories are created **inside the destination folder you choose**. Application support files are written **next to the EXE**, or to the repository root when running from source.

| Path relative to the application | Purpose |
| --- | --- |
| `Настройки/settings.json` | App preferences, including the save destination and automatic update checks |
| `Логи скачивания/` | Runtime logs and downloaded URL records |
| `История ссылок/` | Per-session history of processed download links |
| `Ссылки на скачивания/` | Queued links saved on application exit |
| `Обработать вручную/` | Failed/problematic downloads and files requiring manual attention |
| `Логи проблем/` | Diagnostic sessions, events and reports (when logging is enabled) |
| `Обновления/build-.../` | Staged EXE updates and, if installation fails, `update-error.log` |

**Log retention:** the application automatically cleans up some history and diagnostic files according to their retention windows (60 and 120 days, respectively). Keep a separate copy if you need particular logs for an investigation.

## Application updates

Windows builds are published on [GitHub Releases](https://github.com/zeter1/Universal-Video-Downloader/releases) with tags such as `build-7ab3f9f`. This identifier represents the **exact build revision**, not the human-readable application version; multiple builds may still display `6.3`.

- **Automatic checks:** by default the EXE checks published releases in the background on startup. Disable this using **«Проверять обновления при запуске»** (*Check for updates on startup*).
- **Manual checks:** click **«🔄 Проверить обновления»** (*Check for updates*).
- **Installation:** if a newer recognized build exists, the app offers to download it, verifies its **SHA-256 against the GitHub release asset metadata**, and only after your approval exits to let a separate updater replace the executable. The updater includes an attempt to restore the previous EXE if installation fails.
- **Your data:** the updater is designed not to delete settings, history or downloaded media. The EXE folder must be writable.
- **Older EXEs:** builds without embedded revision information cannot be compared safely and must be **upgraded manually once** by downloading a newer EXE. Source-code launches cannot self-replace an EXE.

**Do not start an update while downloading media.** If GitHub cannot be reached, update checks may fail without preventing normal app use. A passing CI build does not by itself verify a real-life in-place Windows update on every user's PC.

**Updating yt-dlp is different from updating this application.** The packaged EXE has its own bundled yt-dlp. Running `pip install -U yt-dlp` or `Обновление yt-dlp.bat` **does not update the embedded executable inside the packaged app**. Download a newer app release instead; for source installations, update the Python dependency through pip.

## Verifying the downloaded EXE

If desired, download `Universal-Video-Downloader.sha256.txt` **from the same release** as the EXE. In PowerShell, in the directory containing both files:

```powershell
(Get-FileHash .\Universal-Video-Downloader.exe -Algorithm SHA256).Hash
Get-Content .\Universal-Video-Downloader.sha256.txt
```

Compare the 64-character hexadecimal hash with the one at the beginning of the `.sha256.txt` file. **They must match.** A checksum detects whether the downloaded bytes match the published artifact; it is not a substitute for trusting the source and does not constitute a code signature.

## Run from source

This route is intended for developers or users who prefer a Python environment.

**Requirements:** Windows, Python **3.12** (the version used in CI), Git, [FFmpeg](https://ffmpeg.org/) with both `ffmpeg` and `ffprobe` available in `PATH`. `yt-dlp` is installed from `requirements.txt`. [Deno](https://deno.com/) or Node.js is helpful for some YouTube scenarios.

```powershell
git clone https://github.com/zeter1/Universal-Video-Downloader.git
cd Universal-Video-Downloader

py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe video_downloader.py
```

Check the tools:

```powershell
ffmpeg -version
ffprobe -version
.\.venv\Scripts\python.exe -m yt_dlp --version
```

Do not confuse the source environment with the packaged EXE: the development prerequisites are not needed for the Windows Release download. Ubuntu is used for automated tests, but the primary desktop target is Windows.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| **Video will not download** | Network access, whether the URL opens in a browser, site restrictions, current EXE/yt-dlp, **«Не скачалось»** (Failed downloads) |
| **YouTube format error, HTTP 403 or timeout** | Recent yt-dlp, Deno/Node when running from source, VPN/proxy/network restrictions, logs for the specific attempt |
| **Video exists but MP3 is missing** | Whether the source contains an audio stream, FFmpeg messages and audio-duration validation |
| **A URL is skipped** | Previously downloaded video or duplicate in the current queue; check saved history |
| **An update fails to install** | Write access to the EXE directory, active downloads, `Обновления/build-.../update-error.log`; download the latest EXE manually if necessary |
| **Missing tools in source mode** | Ensure `ffmpeg`, `ffprobe` and `yt-dlp` are installed and available through `PATH` / the Python environment |

For a reproducible bug, include the **`build-...` revision**, Windows version, source website, expected vs. actual behavior, reproduction steps, and **redacted** logs. Start with `Логи проблем/latest_run.json` and the corresponding `Логи проблем/Сессии/<run>/` directory if problem diagnostics are enabled.

**[Support instructions](SUPPORT.md)** · **[Open an issue](https://github.com/zeter1/Universal-Video-Downloader/issues/new/choose)** · **[Security policy](SECURITY.md)**

> **Privacy:** remove cookies, access tokens, authorization headers, private URLs and sensitive local paths before sharing diagnostics. Logs may reveal information about websites visited and local files.

## Architecture

The project is split by responsibility so a downloader, UI or diagnostic change does not require modifying one monolithic file.

```text
video_downloader.py → src/bootstrap.py → src/application.py
                                           ├── src/app/          paths and state
                                           ├── src/core/         runtime, settings, subprocess, updates
                                           ├── src/downloads/    URL, queue, yt-dlp, quality, MP4 validation
                                           ├── src/audio/        MP3 conversion, merge and split
                                           ├── src/ui/           Tkinter, notifications, update UI
                                           └── src/diagnostics/  errors, events, reports and retention
tests/               offline regression tests
scripts/             repository navigation and structure checks
.github/workflows/   CI, Windows EXE packaging and GitHub Releases
```

Technical design highlights:

- **Media output contract:** 1080p maximum, preferred compatible formats, actual video validation using ffprobe and MP4 output.
- **Recovery-focused behavior:** yt-dlp fallback strategies, bounded retries, hung-process detection and cancellation without hiding useful errors.
- **Diagnostic evidence:** structured events and sessions, outcome reports, size limits and log retention policies.
- **Windows packaging:** PyInstaller `--onefile`, bundled tools, `--self-test`, SHA-256 and a revision-aware EXE updater.
- **AI-friendly navigation:** clear module owners and a scope-selection script for focused Codex/ChatGPT work.

**Technical documentation:** [Architecture](docs/ARCHITECTURE.md) · [Code map](docs/CODE_MAP.md) · [Module index](docs/CODE_INDEX.md) · [Codex workflow](docs/CODEX_WORKFLOW.md) · [Agent instructions](AGENTS.md)

## Testing and packaging

Run the baseline offline checks:

```powershell
.\.venv\Scripts\python.exe -m compileall -q video_downloader.py problem_log_validator.py src scripts tests
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts/check_codex_efficiency.py
```

In [GitHub Actions](https://github.com/zeter1/Universal-Video-Downloader/actions/workflows/python-check.yml), relevant Python changes trigger compilation and offline tests on Ubuntu. **The Windows EXE is built separately**, after successful tests, through a manually dispatched workflow or a push commit containing `[package]`. The Windows job reruns tests, packages with PyInstaller, runs the bundled-tool `--self-test` and publishes the EXE plus SHA-256 via GitHub Releases.

A README-only change **does not create a new executable**. A passing CI run does not guarantee support for every external website or replace manual GUI and in-place update testing.

## Limitations and security

- **Website access:** compatibility depends on yt-dlp, website restrictions, connectivity and authorization. Access to private, paid or protected content is not guaranteed.
- **Quality:** video output is capped at 1080p; a 1080p stream is not guaranteed to exist.
- **Audio:** MP3 is generated only from successful downloads that have an audio track.
- **Platform:** packaged EXE is for Windows x64 and is not commercially code-signed.
- **Legal use:** download only content you are authorized to access and use, subject to copyright law and each service's terms.
- **License:** this repository does not currently include a standalone `LICENSE` file; publicly visible source code does not automatically grant permission to redistribute or use it commercially.

For potential vulnerabilities, follow [SECURITY.md](SECURITY.md) and do not disclose secrets in a public issue.

## Contact

**Developer:** [ZeTer / GitHub](https://github.com/zeter1)

- **Email:** [zeter11@gmail.com](mailto:zeter11@gmail.com)
- **Telegram:** [https://t.me/zeter1](https://t.me/zeter1)
- **LinkedIn:** [https://www.linkedin.com/in/zeter/](https://www.linkedin.com/in/zeter/)
- **Profiles:** [Facebook](https://www.facebook.com/zeter1) · [VK](https://vk.ru/zeter1) · [Instagram](https://www.instagram.com/zeter1992/) · [TenChat](https://tenchat.ru/zeter) · [X](https://x.com/zeter1) · [Threads](https://www.threads.com/@zeter1992) · [OK](https://ok.ru/zeter1)
- **Telegram group:** [https://t.me/zeterchat](https://t.me/zeterchat)

---

[↑ Back to top](#universal-video-downloader) · [Русская версия](README.md)
