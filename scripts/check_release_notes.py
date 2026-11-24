"""Check that the notes describe the application version being packaged."""

from pathlib import Path
import re
import runpy


ROOT = Path(__file__).resolve().parents[1]


def main():
    constants = runpy.run_path(str(ROOT / "src" / "core" / "constants.py"))
    version = constants["APP_VERSION"]
    with (ROOT / "RELEASE_NOTES_RU.md").open(encoding="utf-8-sig") as notes:
        heading = notes.readline().strip()
    match = re.fullmatch(
        r"# ZeTer Video Downloader — Windows-сборка (\d+\.\d+)(?: \(.+\))?", heading,
    )
    notes_version = match.group(1) if match else "не указана"
    if notes_version != version:
        print(f"Версия приложения {version}, версия в RELEASE_NOTES_RU.md {notes_version}.")
        return 1
    print(f"Заметки к сборке {version} проверены.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
