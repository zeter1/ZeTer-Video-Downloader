"""Проверка yt-dlp, FFmpeg, ffprobe и дополнительных зависимостей."""

import subprocess


class DependenciesMixin:
    def check_dependencies(self) -> None:
        self.missing_deps = []
        self.missing_pip_packages = []
        self.optional_missing_deps = []

        for tool, version_arg in (("yt-dlp", "--version"), ("ffmpeg", "-version"),
                                  ("ffprobe", "-version")):
            try:
                result = subprocess.run(
                    [tool, version_arg], capture_output=True,
                    timeout=5, creationflags=self.subprocess_flags
                )
                if result.returncode == 0:
                    continue
            except (subprocess.SubprocessError, OSError):
                pass
            self.missing_deps.append(tool)
            if tool == "yt-dlp":
                self.missing_pip_packages.append(tool)

        js_runtime_found = False
        js_runtime_name  = None

        for runtime, args in [("deno", ["deno", "--version"]),
                               ("node", ["node", "--version"])]:
            try:
                r = subprocess.run(
                    args, capture_output=True, timeout=5, text=True,
                    encoding='utf-8', errors='replace',
                    creationflags=self.subprocess_flags
                )
                if r.returncode == 0:
                    js_runtime_found = True
                    js_runtime_name = ("Deno" if runtime == "deno"
                                       else f"Node.js {r.stdout.strip()}")
                    break
            except Exception:
                pass

        self.js_runtime_found = js_runtime_found
        self.js_runtime_name  = js_runtime_name
        if not js_runtime_found:
            self.optional_missing_deps.append("JavaScript Runtime (Deno или Node.js)")
