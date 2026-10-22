"""Safe portable Windows EXE updates from this project's GitHub Releases.

GitHub publishes build-<commit> prereleases; /releases/latest excludes them.
The GitHub asset's SHA-256 digest is mandatory before any replacement.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.request

REPOSITORY = "zeter1/ZeTer-Video-Downloader"
# Permit authentic build identities stamped before the repository rename.
LEGACY_REPOSITORY = "zeter1/Universal-Video-Downloader"
RELEASES_URL = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=50"
RELEASES_PAGE = f"https://github.com/{REPOSITORY}/releases"
EXE_NAME = "ZeTer-Video-Downloader.exe"
_TAG = re.compile(r"build-[a-f0-9]{7}$")
_DIGEST = re.compile(r"sha256:([a-fA-F0-9]{64})$")


def installed_build_tag():
    """Only packaged, stamped executables may replace themselves."""
    if not getattr(sys, "frozen", False):
        return None
    try:
        manifest = Path(sys._MEIPASS) / "build_info.json"
        payload = json.loads(manifest.read_text(encoding="utf-8-sig"))
        tag = payload.get("tag", "")
        commit = payload.get("commit", "")
        if (payload.get("repository") in (REPOSITORY, LEGACY_REPOSITORY) and _TAG.fullmatch(tag)
                and re.fullmatch(r"[0-9a-f]{40}", commit)
                and tag == "build-" + commit[:7]):
            return tag
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return None


def parse_releases(data, current_tag):
    """Return (status, candidate). Fail closed for unknown/unverified builds."""
    if not current_tag or not _TAG.fullmatch(current_tag):
        return "unidentified", None
    valid = []
    for item in data:
        if not isinstance(item, dict) or item.get("draft"):
            continue
        tag = item.get("tag_name", "")
        if not isinstance(tag, str) or not _TAG.fullmatch(tag):
            continue
        for asset in item.get("assets", []):
            if asset.get("name") != EXE_NAME or asset.get("state") != "uploaded":
                continue
            digest = asset.get("digest") or ""
            match = _DIGEST.fullmatch(digest)
            url = asset.get("browser_download_url", "")
            prefix = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{EXE_NAME}"
            if match and url == prefix:
                valid.append({
                    "tag": tag,
                    "published_at": item.get("published_at") or "",
                    "url": url,
                    "digest": match.group(1).lower(),
                    "release_url": item.get("html_url") or RELEASES_PAGE,
                })
                break
    valid.sort(key=lambda x: x["published_at"], reverse=True)
    if not valid:
        return "unavailable", None
    tags = [release["tag"] for release in valid]
    if current_tag not in tags:
        return "unidentified", None
    if tags[0] == current_tag:
        return "current", None
    return "available", valid[0] if tags.index(current_tag) > 0 else None


def check_for_update(current_tag, opener=None):
    """HTTPS read with finite timeout; never called on the Tk thread."""
    request = urllib.request.Request(
        RELEASES_URL, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "ZeTer-Video-Downloader-Updater",
        },
    )
    opener = opener or urllib.request.urlopen
    with opener(request, timeout=15) as response:
        data = json.load(response)
    if not isinstance(data, list):
        raise ValueError("GitHub returned an unexpected releases document")
    return parse_releases(data, current_tag)


def download_verified(release, directory, opener=None):
    """Stream a bounded executable, validate SHA256, and atomically stage it."""
    tag = release["tag"]
    expected = release["digest"]
    if not _TAG.fullmatch(tag) or not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise ValueError("Invalid release identity or checksum")
    expected_url = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{EXE_NAME}"
    if release["url"] != expected_url:
        raise ValueError("Untrusted release download URL")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / EXE_NAME
    partial = directory / (EXE_NAME + ".part")
    opener = opener or urllib.request.urlopen
    hasher = hashlib.sha256()
    total = 0
    try:
        with opener(urllib.request.Request(
            expected_url, headers={"User-Agent": "ZeTer-Video-Downloader-Updater"}
        ), timeout=45) as response, open(partial, "wb") as output:
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > 400 * 1024 * 1024:
                    raise ValueError("Update exceeds allowed size")
                hasher.update(block)
                output.write(block)
        if total < 1024 or hasher.hexdigest() != expected:
            raise ValueError("Update SHA-256 verification failed")
        os.replace(partial, destination)
        return destination
    finally:
        partial.unlink(missing_ok=True)


# The helper runs separately after the application's EXE has exited.
# Never touch Settings, Logs or downloaded media.
_HELPER = r"""
param([int]$ParentPid, [string]$Source, [string]$Target, [string]$ExpectedHash)
$ErrorActionPreference = 'Stop'
$backup = Join-Path $PSScriptRoot 'previous.exe'
try {
  $watch = [Diagnostics.Stopwatch]::StartNew()
  while (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue) {
    if ($watch.Elapsed.TotalSeconds -gt 120) { throw 'Application did not exit in time' }
    Start-Sleep -Milliseconds 500
  }
  if ((Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ExpectedHash) {
    throw 'Downloaded update checksum mismatch'
  }
  if (Test-Path -LiteralPath $backup) { throw 'Previous update backup still exists' }
  Move-Item -LiteralPath $Target -Destination $backup -ErrorAction Stop
  try {
    Copy-Item -LiteralPath $Source -Destination $Target -ErrorAction Stop
    if ((Get-FileHash -LiteralPath $Target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ExpectedHash) {
      throw 'Installed update checksum mismatch'
    }
    Start-Process -FilePath $Target -ErrorAction Stop
  } catch {
    if (Test-Path -LiteralPath $Target) { Remove-Item -LiteralPath $Target -Force }
    Move-Item -LiteralPath $backup -Destination $Target -ErrorAction Stop
    throw
  }
  Remove-Item -LiteralPath $backup -Force
  Remove-Item -LiteralPath $Source -Force
} catch {
  Add-Content -LiteralPath (Join-Path $PSScriptRoot 'update-error.log') -Value (
    (Get-Date).ToString('o') + ' ' + $_.Exception.Message
  )
}
"""


def start_verified_replacement(source, target, expected_hash):
    """Spawn a detached updater. Caller must then close the application."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        raise RuntimeError("Self-updating is supported only for packaged Windows EXE")
    source, target = Path(source).resolve(), Path(target).resolve()
    if source == target or not target.is_file() or source.name != EXE_NAME:
        raise ValueError("Invalid update source or target")
    if not re.fullmatch(r"[a-f0-9]{64}", expected_hash):
        raise ValueError("Invalid SHA-256")
    helper = source.parent / "apply-update.ps1"
    helper.write_text(_HELPER, encoding="utf-8-sig")
    subprocess.Popen(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-File", str(helper),
         "-ParentPid", str(os.getpid()), "-Source", str(source),
         "-Target", str(target), "-ExpectedHash", expected_hash],
        creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS),
        close_fds=True,
    )
