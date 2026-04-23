from __future__ import annotations

import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from app.settings.logging import log_runtime_event


class DownloadCancelledError(Exception):
    def __init__(self, asset_name: str):
        super().__init__(f"Download cancelled: {asset_name}")
        self.asset_name = asset_name


def fetch_remote_file_size(download_url: str, request_headers: dict[str, str] | None = None) -> int | None:
    request = urllib.request.Request(download_url, headers=request_headers or {}, method="HEAD")
    with urllib.request.urlopen(request, timeout=30) as response:
        return get_content_length(response)


def download_file(
    download_url: str,
    destination: Path,
    asset_name: str,
    asset_index: int,
    total_assets: int,
    request_headers: dict[str, str] | None = None,
    render_progress: Callable[[str, int, float | None], None] | None = None,
) -> None:
    request = urllib.request.Request(download_url, headers=request_headers)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_destination = destination.with_suffix(destination.suffix + ".part")
    log_runtime_event(
        f"download start | asset={asset_name} | destination={destination} | temp_destination={temp_destination} | "
        f"url={download_url}"
    )
    try:
        os.system("cls")
        with urllib.request.urlopen(request, timeout=120) as response, temp_destination.open("wb") as output:
            total_size = get_content_length(response)
            downloaded = 0
            next_report_percent = 0
            last_unknown_report_at = 0.0
            started_at = time.monotonic()
            if render_progress is None:
                print(f"[INFO] Downloading asset {asset_index}/{total_assets}: {asset_name}")

            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break

                output.write(chunk)
                downloaded += len(chunk)
                next_report_percent, last_unknown_report_at = _report_download_progress(
                    asset_name,
                    downloaded,
                    total_size,
                    next_report_percent,
                    last_unknown_report_at,
                    started_at,
                    render_progress,
                )

        temp_destination.replace(destination)
        log_runtime_event(f"download complete | asset={asset_name} | destination={destination} | bytes={downloaded}")

        if total_size is None:
            if render_progress is None:
                _finish_progress_line(f"[INFO] Download complete: {asset_name} ({_format_size(downloaded)})")
        elif next_report_percent <= 100:
            if render_progress is not None:
                render_progress(asset_name, 100, downloaded / (1024 * 1024) / max(time.monotonic() - started_at, 0.001))
            else:
                _finish_progress_line(
                    f"[INFO] Download complete: {asset_name} (100%, {_format_size(downloaded)})"
                )
    except KeyboardInterrupt as exc:
        temp_destination.unlink(missing_ok=True)
        log_runtime_event(f"download cancelled | asset={asset_name} | temp_destination={temp_destination}")
        raise DownloadCancelledError(asset_name) from exc
    except urllib.error.URLError as exc:
        temp_destination.unlink(missing_ok=True)
        log_runtime_event(f"download urlerror | asset={asset_name} | error={exc!r}")
        raise RuntimeError(f"Failed to download {download_url}") from exc
    except Exception as exc:
        temp_destination.unlink(missing_ok=True)
        log_runtime_event(f"download failed | asset={asset_name} | error={exc!r}")
        raise


def get_content_length(response: object) -> int | None:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None

    content_length = headers.get("Content-Length")
    if not content_length:
        return None

    try:
        return int(content_length)
    except ValueError:
        return None


def _get_content_length(response: object) -> int | None:
    return get_content_length(response)


def _report_download_progress(
    asset_name: str,
    downloaded: int,
    total_size: int | None,
    next_report_percent: int,
    last_unknown_report_at: float,
    started_at: float,
    render_progress: Callable[[str, int, float | None], None] | None,
) -> tuple[int, float]:
    elapsed = max(time.monotonic() - started_at, 0.001)
    speed_mbps = downloaded / (1024 * 1024) / elapsed

    if total_size is None or total_size <= 0:
        now = time.monotonic()
        if now - last_unknown_report_at >= 0.25:
            if render_progress is not None:
                render_progress(asset_name, 0, speed_mbps)
            else:
                _render_progress_line(f"[INFO] {asset_name}: {_format_size(downloaded)} downloaded...")
            last_unknown_report_at = now
        return next_report_percent, last_unknown_report_at

    percent = int(downloaded * 100 / total_size)
    if render_progress is not None:
        if percent >= next_report_percent and next_report_percent <= 100:
            render_progress(asset_name, percent, speed_mbps)
            next_report_percent = percent + 1
        return next_report_percent, last_unknown_report_at

    while percent >= next_report_percent and next_report_percent <= 100:
        _render_progress_line(
            f"[INFO] {asset_name}: {next_report_percent}% "
            f"({_format_size(downloaded)} / {_format_size(total_size)})"
        )
        next_report_percent += 10

    return next_report_percent, last_unknown_report_at


def _format_size(size_in_bytes: int) -> str:
    return f"{size_in_bytes / (1024 * 1024):.1f} MB"


def _render_progress_line(message: str) -> None:
    width = shutil.get_terminal_size(fallback=(100, 20)).columns
    padded_message = message.ljust(max(width - 1, len(message)))
    sys.stdout.write(f"\r{padded_message}")
    sys.stdout.flush()


def _finish_progress_line(message: str) -> None:
    _render_progress_line(message)
    sys.stdout.write("\n")
    sys.stdout.flush()
