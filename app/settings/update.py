from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from app.settings.config import APP_ROOT, DATA_ROOT
from app.settings.default import APP_VERSION, UPDATE_ASSET_KEYWORDS, UPDATE_REPOSITORY
from app.settings.downloads import DownloadCancelledError, download_file
from app.settings.logging import log_runtime_event
from app.ui import render_download_progress_screen


GITHUB_API = "https://api.github.com/repos/{repository}/releases/latest"
UPDATE_DIR_NAME = "update"
INSTALL_SCRIPT_NAME = "install_update.ps1"
USER_AGENT = "noveltrans-updater"


@dataclass(frozen=True)
class UpdateAsset:
    name: str
    download_url: str


@dataclass(frozen=True)
class UpdateRelease:
    version: str
    tag_name: str
    html_url: str
    asset: UpdateAsset


class UpdateNotConfiguredError(RuntimeError):
    pass


def get_current_version() -> str:
    version_path = _get_version_file_path()
    try:
        version = version_path.read_text(encoding="utf-8").strip()
    except OSError:
        return APP_VERSION
    return version or APP_VERSION


def get_startup_update_status() -> str:
    current_version = get_current_version()
    rollback_error_log = _get_update_error_log_path()
    if rollback_error_log.exists():
        return f"[WARN] 업데이트 설치에 실패하여 이전 버전으로 롤백했습니다. 현재 버전: {current_version}"

    try:
        release = check_for_update()
    except UpdateNotConfiguredError as exc:
        log_runtime_event(f"startup update check skipped | error={exc!r}")
        return f"[INFO] 업데이트 확인 건너뜀: {exc}"
    except Exception as exc:
        log_runtime_event(f"startup update check failed | error={exc!r}")
        return f"[INFO] 업데이트 확인 실패: {exc}"

    if release is None:
        return f"[INFO] 최신 버전입니다. 현재 버전: {current_version}"

    return f"[INFO] 새 버전이 있습니다: {current_version} -> {release.tag_name}. 설정 메뉴에서 업데이트할 수 있습니다."


def clear_staged_update_files() -> None:
    update_dir = DATA_ROOT / UPDATE_DIR_NAME
    if not update_dir.exists():
        return

    for child in update_dir.iterdir():
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        except OSError as exc:
            log_runtime_event(f"update cleanup skipped | path={child} | error={exc!r}")


def _get_update_error_log_path() -> Path:
    return DATA_ROOT / UPDATE_DIR_NAME / "update_error.log"


def check_for_update() -> UpdateRelease | None:
    release = get_latest_release()
    latest_version = release.version
    current_version = _normalize_version(get_current_version())
    if _compare_versions(latest_version, current_version) <= 0:
        return None

    return release


def get_latest_release() -> UpdateRelease:
    repository = _get_update_repository()
    release = _fetch_latest_release(repository)
    latest_version = _normalize_version(str(release.get("tag_name", "")))
    asset = _select_release_asset(release)
    return UpdateRelease(
        version=latest_version,
        tag_name=str(release.get("tag_name", latest_version)),
        html_url=str(release.get("html_url", "")),
        asset=asset,
    )


def run_update_flow() -> tuple[str, bool]:
    current_version = get_current_version()
    try:
        release = get_latest_release()
    except UpdateNotConfiguredError as exc:
        return f"[WARN] {exc}", False
    except Exception as exc:
        log_runtime_event(f"update check failed | error={exc!r}")
        return f"[ERROR] 업데이트 확인에 실패했습니다: {exc}", False

    if not getattr(sys, "frozen", False):
        if _compare_versions(release.version, _normalize_version(current_version)) <= 0:
            return (
                f"[INFO] 최신버전입니다. 현재 버전: {current_version}. "
                "업데이트 재설치는 빌드된 exe에서만 사용할 수 있습니다.",
                False,
            )
        return (
            f"[INFO] 업데이트 가능: {current_version} -> {release.tag_name}. "
            "설치는 빌드된 exe에서만 사용할 수 있습니다.",
            False,
        )

    if _compare_versions(release.version, _normalize_version(current_version)) > 0:
        print(f"[INFO] 업데이트 가능: {current_version} -> {release.tag_name}")
    else:
        print(f"[INFO] 최신버전입니다. 현재 버전: {current_version}, 최신 릴리즈: {release.tag_name}")
    print(f"[INFO] 파일: {release.asset.name}")
    print("지금 업데이트할까요? (y/n)")
    if input("").strip().lower() != "y":
        return "[INFO] 업데이트를 취소했습니다.", False

    try:
        update_zip = download_and_stage_update(release)
        start_update_installer(update_zip)
    except DownloadCancelledError as exc:
        return f"[INFO] 업데이트 다운로드를 취소했습니다: {exc.asset_name}", False
    except Exception as exc:
        log_runtime_event(f"update install preparation failed | error={exc!r}")
        return f"[ERROR] 업데이트 준비에 실패했습니다: {exc}", False

    return "[INFO] 업데이트 설치기를 시작했습니다. NovelTrans를 종료합니다.", True


def download_and_stage_update(release: UpdateRelease) -> Path:
    update_dir = DATA_ROOT / UPDATE_DIR_NAME
    update_dir.mkdir(parents=True, exist_ok=True)
    destination = update_dir / release.asset.name
    destination.unlink(missing_ok=True)
    download_file(
        release.asset.download_url,
        destination,
        release.asset.name,
        1,
        1,
        request_headers={"User-Agent": USER_AGENT},
        render_progress=lambda asset_name, percent, speed_mbps: render_download_progress_screen(
            title="NovelTrans 업데이트",
            message=f"{release.tag_name} 업데이트 파일을 다운로드하는 중입니다.",
            item_label="파일",
            item_name=asset_name,
            destination_path=str(destination),
            percent=max(0, min(percent, 100)),
            speed_mbps=speed_mbps,
        ),
    )
    return destination


def start_update_installer(update_zip: Path) -> None:
    if not getattr(sys, "frozen", False):
        raise RuntimeError("업데이트 설치는 빌드된 exe에서만 사용할 수 있습니다.")

    script_path = _write_installer_script(update_zip)
    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
        "-AppRoot",
        str(APP_ROOT),
        "-ZipPath",
        str(update_zip),
        "-ExeName",
        Path(sys.executable).name,
        "-ParentPid",
        str(os.getpid()),
    ]
    log_runtime_event(f"starting update installer | script={script_path} | zip={update_zip}")
    subprocess.Popen(command, cwd=str(APP_ROOT), close_fds=True)


def _get_update_repository() -> str:
    repository = os.getenv("NOVELTRANS_UPDATE_REPOSITORY", UPDATE_REPOSITORY).strip()
    if not repository:
        raise UpdateNotConfiguredError(
            "업데이트 저장소가 설정되지 않았습니다. UPDATE_REPOSITORY 또는 NOVELTRANS_UPDATE_REPOSITORY를 설정하세요."
        )
    if "/" not in repository:
        raise UpdateNotConfiguredError(f"잘못된 업데이트 저장소입니다: {repository}")
    return repository


def _get_version_file_path() -> Path:
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base_path / "app" / "version.txt"


def _fetch_latest_release(repository: str) -> dict[str, object]:
    request = urllib.request.Request(
        GITHUB_API.format(repository=repository),
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _select_release_asset(release: dict[str, object]) -> UpdateAsset:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise RuntimeError("최신 릴리즈에 asset이 없습니다.")

    zip_assets = [
        asset
        for asset in assets
        if isinstance(asset, dict)
        and str(asset.get("name", "")).lower().endswith(".zip")
        and str(asset.get("browser_download_url", ""))
    ]
    if not zip_assets:
        raise RuntimeError("최신 릴리즈에 다운로드 가능한 zip asset이 없습니다.")

    keywords = [keyword.lower() for keyword in UPDATE_ASSET_KEYWORDS if keyword]
    matching_assets = [
        asset
        for asset in zip_assets
        if all(keyword in str(asset.get("name", "")).lower() for keyword in keywords)
    ]
    selected = matching_assets[0] if matching_assets else zip_assets[0]
    return UpdateAsset(
        name=str(selected["name"]),
        download_url=str(selected["browser_download_url"]),
    )


def _normalize_version(version: str) -> str:
    normalized = version.strip()
    if normalized.lower().startswith("v"):
        normalized = normalized[1:]
    return normalized


def _compare_versions(left: str, right: str) -> int:
    left_parts = _version_parts(left)
    right_parts = _version_parts(right)
    max_len = max(len(left_parts), len(right_parts))
    left_parts.extend([0] * (max_len - len(left_parts)))
    right_parts.extend([0] * (max_len - len(right_parts)))
    return (left_parts > right_parts) - (left_parts < right_parts)


def _version_parts(version: str) -> list[int]:
    parts = [int(part) for part in re.findall(r"\d+", version)]
    return parts or [0]


def _write_installer_script(update_zip: Path) -> Path:
    update_dir = update_zip.parent
    script_path = update_dir / INSTALL_SCRIPT_NAME
    script_path.write_text(_INSTALLER_SCRIPT, encoding="utf-8")
    return script_path


_INSTALLER_SCRIPT = r"""
param(
    [Parameter(Mandatory=$true)][string]$AppRoot,
    [Parameter(Mandatory=$true)][string]$ZipPath,
    [Parameter(Mandatory=$true)][string]$ExeName,
    [Parameter(Mandatory=$true)][int]$ParentPid
)

$ErrorActionPreference = "Stop"
$appRootPath = Resolve-Path -LiteralPath $AppRoot
$zipFile = Resolve-Path -LiteralPath $ZipPath
$updateRoot = Split-Path -Parent $zipFile
$extractDir = Join-Path $updateRoot "extracted"
$backupDir = Join-Path $updateRoot ("backup-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
$currentExe = Join-Path $appRootPath $ExeName
$currentInternal = Join-Path $appRootPath "_internal"
$targetData = Join-Path $appRootPath "data"
$targetDict = Join-Path $targetData "dict"
$backupExe = Join-Path $backupDir $ExeName
$backupInternal = Join-Path $backupDir "_internal"
$backupData = Join-Path $backupDir "data"
$backupDict = Join-Path $backupData "dict"

function Restore-Path([string]$BackupPath, [string]$DestinationPath) {
    if (-not (Test-Path -LiteralPath $BackupPath)) {
        return
    }

    Remove-Item -LiteralPath $DestinationPath -Recurse -Force -ErrorAction SilentlyContinue
    $destinationParent = Split-Path -Parent $DestinationPath
    if ($destinationParent) {
        New-Item -ItemType Directory -Path $destinationParent -Force | Out-Null
    }
    Move-Item -LiteralPath $BackupPath -Destination $DestinationPath -Force
}

function Find-PayloadRoot([string]$Root) {
    $directExe = Join-Path $Root $ExeName
    $directInternal = Join-Path $Root "_internal"
    if ((Test-Path -LiteralPath $directExe) -and (Test-Path -LiteralPath $directInternal)) {
        return $Root
    }

    $children = Get-ChildItem -LiteralPath $Root -Directory
    foreach ($child in $children) {
        $childExe = Join-Path $child.FullName $ExeName
        $childInternal = Join-Path $child.FullName "_internal"
        if ((Test-Path -LiteralPath $childExe) -and (Test-Path -LiteralPath $childInternal)) {
            return $child.FullName
        }
    }

    throw "Update zip must contain $ExeName and _internal."
}

try {
    Wait-Process -Id $ParentPid -Timeout 120 -ErrorAction SilentlyContinue

    Remove-Item -LiteralPath $extractDir -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $extractDir | Out-Null
    Expand-Archive -LiteralPath $zipFile -DestinationPath $extractDir -Force
    $payloadRoot = Find-PayloadRoot $extractDir

    New-Item -ItemType Directory -Path $backupDir | Out-Null
    if (Test-Path -LiteralPath $currentExe) {
        Move-Item -LiteralPath $currentExe -Destination $backupDir -Force
    }
    if (Test-Path -LiteralPath $currentInternal) {
        Move-Item -LiteralPath $currentInternal -Destination $backupDir -Force
    }
    if (Test-Path -LiteralPath $targetDict) {
        New-Item -ItemType Directory -Path $backupData -Force | Out-Null
        Move-Item -LiteralPath $targetDict -Destination $backupData -Force
    }

    Copy-Item -LiteralPath (Join-Path $payloadRoot $ExeName) -Destination $appRootPath -Force
    Copy-Item -LiteralPath (Join-Path $payloadRoot "_internal") -Destination $appRootPath -Recurse -Force
    $payloadDict = Join-Path $payloadRoot "data\dict"
    if (Test-Path -LiteralPath $payloadDict) {
        New-Item -ItemType Directory -Path $targetData -Force | Out-Null
        Remove-Item -LiteralPath $targetDict -Recurse -Force -ErrorAction SilentlyContinue
        Copy-Item -LiteralPath $payloadDict -Destination $targetData -Recurse -Force
    }

    Start-Process -FilePath (Join-Path $appRootPath $ExeName) -WorkingDirectory $appRootPath
} catch {
    Restore-Path $backupExe $currentExe
    Restore-Path $backupInternal $currentInternal
    if (Test-Path -LiteralPath $backupDict) {
        Restore-Path $backupDict $targetDict
    } else {
        Remove-Item -LiteralPath $targetDict -Recurse -Force -ErrorAction SilentlyContinue
    }
    Add-Content -LiteralPath (Join-Path $updateRoot "update_error.log") -Value $_.Exception.ToString()
    Start-Process -FilePath (Join-Path $appRootPath $ExeName) -WorkingDirectory $appRootPath
}
"""


__all__ = [
    "check_for_update",
    "clear_staged_update_files",
    "get_current_version",
    "get_latest_release",
    "get_startup_update_status",
    "run_update_flow",
]
