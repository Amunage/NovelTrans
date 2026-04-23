from __future__ import annotations

import subprocess
import sys
from pathlib import Path


APP_EXE_NAME = "app.exe"
APP_RUNTIME_DIR = "runtime"


def get_launcher_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def get_app_executable() -> Path:
    return get_launcher_root() / APP_RUNTIME_DIR / APP_EXE_NAME


def main() -> int:
    app_executable = get_app_executable()
    if not app_executable.is_file():
        print(f"[ERROR] Target executable not found: {app_executable}")
        return 1

    try:
        completed = subprocess.run([str(app_executable), *sys.argv[1:]], cwd=str(app_executable.parent))
    except OSError as exc:
        print(f"[ERROR] Failed to launch target executable: {exc}")
        return 1

    return int(completed.returncode)


if __name__ == "__main__":
    sys.exit(main())
