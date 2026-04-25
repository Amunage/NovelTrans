from __future__ import annotations

from pathlib import Path

from app.settings.config import get_runtime_settings
from app.settings.prompt import SEPARATOR_LINE
from app.ui.control import parse_command, wait_for_enter
from app.ui.render import (
    render_merge_complete_screen,
    render_merge_group_size_screen,
    render_merge_selection_screen,
)
from app.ui.validators import validate_menu_number


MERGED_DIR_NAME = "merged"


def _find_translated_novels(output_root: Path) -> list[Path]:
    if not output_root.is_dir():
        return []
    return sorted([path for path in output_root.iterdir() if path.is_dir()], key=lambda path: path.name.lower())


def _find_translated_chapters(novel_dir: Path) -> list[Path]:
    return sorted(
        [
            path
            for path in novel_dir.glob("*_ko.txt")
            if path.is_file() and path.parent == novel_dir
        ],
        key=lambda path: path.name.lower(),
    )


def _chunk_files(files: list[Path], group_size: int) -> list[list[Path]]:
    if group_size == 0:
        return [files]
    return [files[index : index + group_size] for index in range(0, len(files), group_size)]


def _build_merged_filename(files: list[Path], group_index: int, total_groups: int) -> str:
    first_stem = files[0].stem.replace("_ko", "")
    last_stem = files[-1].stem.replace("_ko", "")
    if total_groups == 1:
        return f"{first_stem}-{last_stem}_merged.txt"
    return f"{group_index:03d}_{first_stem}-{last_stem}_merged.txt"


def _merge_files(novel_dir: Path, files: list[Path], group_size: int) -> tuple[Path, list[Path]]:
    output_dir = novel_dir / MERGED_DIR_NAME
    output_dir.mkdir(parents=True, exist_ok=True)
    groups = _chunk_files(files, group_size)
    output_files: list[Path] = []

    for group_index, group in enumerate(groups, start=1):
        output_path = output_dir / _build_merged_filename(group, group_index, len(groups))
        content_parts = [path.read_text(encoding="utf-8").strip() for path in group]
        separator = f"\n\n{SEPARATOR_LINE}\n\n"
        output_path.write_text(separator.join(part for part in content_parts if part) + "\n", encoding="utf-8")
        output_files.append(output_path)

    return output_dir, output_files


def main() -> int:
    settings = get_runtime_settings()
    output_root = settings.output_root
    status_message: str | None = None

    while True:
        novel_dirs = _find_translated_novels(output_root)
        if not novel_dirs:
            render_merge_selection_screen(
                output_root=output_root,
                novel_dirs=[],
                status_message=f"[WARN] 병합할 번역 폴더가 없습니다: {output_root}",
            )
            wait_for_enter()
            return 0

        render_merge_selection_screen(
            output_root=output_root,
            novel_dirs=novel_dirs,
            status_message=status_message,
        )
        raw = input("").strip()
        command = parse_command(raw)
        if command == "back":
            return 0

        status_message = validate_menu_number(raw, len(novel_dirs))
        if status_message is not None:
            continue

        selected_novel = novel_dirs[int(raw) - 1]
        chapter_files = _find_translated_chapters(selected_novel)
        if not chapter_files:
            status_message = f"[WARN] 번역된 챕터 파일이 없습니다: {selected_novel}"
            continue

        group_status: str | None = None
        while True:
            render_merge_group_size_screen(
                novel_dir=selected_novel,
                chapter_count=len(chapter_files),
                status_message=group_status,
            )
            group_raw = input("묶음 개수: ").strip()
            group_command = parse_command(group_raw)
            if group_command == "back":
                status_message = None
                break

            try:
                group_size = int(group_raw)
            except ValueError:
                group_status = "[ERROR] 0 이상의 정수를 입력해 주세요."
                continue

            if group_size < 0:
                group_status = "[ERROR] 0 이상의 정수를 입력해 주세요."
                continue
            if group_size > len(chapter_files):
                group_status = f"[ERROR] 0 또는 1~{len(chapter_files)} 범위로 입력해 주세요."
                continue

            output_dir, output_files = _merge_files(selected_novel, chapter_files, group_size)
            render_merge_complete_screen(
                novel_name=selected_novel.name,
                output_dir=output_dir,
                output_files=output_files,
                status_message="[INFO] 병합이 완료되었습니다.",
            )
            wait_for_enter()
            return 0
