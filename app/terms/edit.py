from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from app.settings.config import DATA_ROOT
from app.settings.logging import log_runtime_event
from app.translation.engine import atomic_write_text
from app.ui.control import parse_command, wait_for_enter
from app.ui.render import render_glossary_edit_file_selection_screen
from app.ui.validators import validate_menu_number


GLOSSARY_SEPARATOR = ":"


def _find_glossary_files() -> list[Path]:
    glossary_dir = DATA_ROOT / "glossary"
    if not glossary_dir.is_dir():
        return []
    return sorted(glossary_dir.glob("*.json"), key=lambda path: (path.name != "default.json", path.name.lower()))


def _load_glossary(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("용어집 JSON은 객체 형식이어야 합니다.")

    glossary: dict[str, str] = {}
    for source, target in data.items():
        if not isinstance(source, str) or not isinstance(target, str):
            raise ValueError("용어집 JSON의 키와 값은 모두 문자열이어야 합니다.")
        glossary[source] = target
    return glossary


def _build_edit_text(glossary: dict[str, str]) -> str:
    lines = [f"{source}{GLOSSARY_SEPARATOR} {target}" for source, target in glossary.items()]
    return "\n".join(lines).rstrip() + "\n"


def _parse_edit_text(text: str) -> dict[str, str]:
    glossary: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if GLOSSARY_SEPARATOR not in line:
            raise ValueError(f"{line_number}번째 줄에 '{GLOSSARY_SEPARATOR}' 구분자가 없습니다.")

        source, target = line.split(GLOSSARY_SEPARATOR, 1)
        source = source.strip()
        target = target.strip()
        if not source:
            raise ValueError(f"{line_number}번째 줄의 원문 용어가 비어 있습니다.")
        if source in glossary:
            raise ValueError(f"{line_number}번째 줄의 원문 용어가 중복되었습니다: {source}")
        glossary[source] = target
    return glossary


def _build_edit_path(glossary_path: Path) -> Path:
    return glossary_path.with_name(f"{glossary_path.stem}_edit.txt")


def _open_in_editor(path: Path) -> None:
    if os.name == "nt":
        subprocess.run(["notepad.exe", str(path)], check=False)
        return

    editor = os.environ.get("EDITOR")
    if editor:
        subprocess.run([editor, str(path)], check=False)
        return

    print(f"[INFO] 편집기로 열 수 없어 파일 경로를 표시합니다: {path}")
    wait_for_enter()


def _edit_glossary_file(glossary_path: Path) -> int:
    glossary = _load_glossary(glossary_path)
    edit_path = _build_edit_path(glossary_path)
    original_edit_text = _build_edit_text(glossary)
    atomic_write_text(edit_path, original_edit_text)

    _open_in_editor(edit_path)

    edited_text = edit_path.read_text(encoding="utf-8")
    parsed_glossary = _parse_edit_text(edited_text)
    atomic_write_text(glossary_path, json.dumps(parsed_glossary, ensure_ascii=False, indent=2) + "\n")

    try:
        edit_path.unlink()
    except OSError:
        pass

    log_runtime_event(
        f"glossary edit saved | glossary={glossary_path} | entries={len(parsed_glossary)}"
    )
    return len(parsed_glossary)


def main() -> int:
    status_message: str | None = None

    while True:
        glossary_files = _find_glossary_files()
        if not glossary_files:
            render_glossary_edit_file_selection_screen(
                glossary_files=[],
                status_message=f"[WARN] 용어집 파일이 없습니다: {DATA_ROOT / 'glossary'}",
            )
            wait_for_enter()
            return 0

        render_glossary_edit_file_selection_screen(
            glossary_files=glossary_files,
            status_message=status_message,
        )
        raw = input("").strip()
        command = parse_command(raw)
        if command == "back":
            return 0

        status_message = validate_menu_number(raw, len(glossary_files))
        if status_message is not None:
            continue

        glossary_path = glossary_files[int(raw) - 1]
        try:
            entry_count = _edit_glossary_file(glossary_path)
        except Exception as exc:
            log_runtime_event(f"glossary edit failed | glossary={glossary_path} | error={exc!r}")
            status_message = f"[ERROR] 용어집을 저장하지 않았습니다: {exc}"
            continue

        status_message = f"[INFO] 용어집을 저장했습니다: {glossary_path.name} ({entry_count}개)"
