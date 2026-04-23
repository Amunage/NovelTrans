from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

from app.settings.config import DATA_ROOT


LOG_FILE_NAME = "app_log.log"
MAX_LOG_RUNS = 5
LOG_RUN_MARKER = "=== NOVELTRANS RUN START ==="
_LOGGING_INITIALIZED = False


def get_log_path() -> Path:
    primary_path = DATA_ROOT / LOG_FILE_NAME
    try:
        primary_path.parent.mkdir(parents=True, exist_ok=True)
        return primary_path
    except OSError:
        fallback_dir = Path(tempfile.gettempdir()) / "noveltrans"
        fallback_dir.mkdir(parents=True, exist_ok=True)
        return fallback_dir / LOG_FILE_NAME


def log_runtime_event(message: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_path = get_log_path()
    try:
        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(f"[{timestamp}] {message}\n")
    except OSError:
        pass


def initialize_runtime_logging() -> None:
    global _LOGGING_INITIALIZED

    if _LOGGING_INITIALIZED:
        return

    log_path = get_log_path()
    try:
        _prune_old_log_runs(log_path)
        with log_path.open("a", encoding="utf-8") as log_file:
            separator = "\n" if log_path.stat().st_size > 0 else ""
            log_file.write(f"{separator}{LOG_RUN_MARKER} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    except OSError:
        pass

    _LOGGING_INITIALIZED = True


def _prune_old_log_runs(log_path: Path) -> None:
    if not log_path.exists():
        return

    try:
        text = log_path.read_text(encoding="utf-8")
    except OSError:
        return

    if text.startswith("current_log="):
        log_path.write_text("", encoding="utf-8")
        return

    run_starts = [index for index, line in enumerate(text.splitlines()) if line.startswith(LOG_RUN_MARKER)]
    if len(run_starts) < MAX_LOG_RUNS:
        return

    lines = text.splitlines(keepends=True)
    keep_start = run_starts[-(MAX_LOG_RUNS - 1)] if MAX_LOG_RUNS > 1 else len(lines)
    log_path.write_text("".join(lines[keep_start:]), encoding="utf-8")


initialize_runtime_logging()


__all__ = ["get_log_path", "initialize_runtime_logging", "log_runtime_event"]