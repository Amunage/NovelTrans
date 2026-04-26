from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from app.settings.config import get_runtime_settings
from app.settings.logging import log_runtime_event
from app.translation.engine import atomic_write_text, build_translated_document
from app.ui.control import parse_command, wait_for_enter
from app.ui.render import render_review_file_selection_screen, render_review_selection_screen
from app.ui.validators import validate_menu_number
from app.utils import find_translated_novels, parse_source_file


REVIEW_DIR_NAME = "review"
REVIEW_SUFFIX = "_ko_review"
REVIEW_BLOCK_PATTERN = re.compile(r"\s*\{\{(.*?)\}\}\s*\[\[(.*?)\]\]", flags=re.DOTALL)


def _find_review_files(novel_dir: Path) -> list[Path]:
    review_dir = novel_dir / REVIEW_DIR_NAME
    if not review_dir.is_dir():
        return []
    return sorted(
        [path for path in review_dir.glob(f"*{REVIEW_SUFFIX}.txt") if path.is_file()],
        key=lambda path: path.name.lower(),
    )


def _build_final_translation_path(review_file: Path) -> Path:
    stem = review_file.stem
    final_stem = stem[: -len("_review")] if stem.endswith("_review") else stem
    return review_file.parent.parent / f"{final_stem}.txt"


def _parse_review_blocks(review_text: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    position = 0

    for match in REVIEW_BLOCK_PATTERN.finditer(review_text):
        if review_text[position : match.start()].strip():
            raise ValueError("review 파일의 {{원문}} [[번역문]] 블록 밖에 텍스트가 있습니다.")

        source_text = match.group(1).strip()
        translated_text = match.group(2).strip()
        if not source_text:
            raise ValueError("review 파일에 비어 있는 {{원문}} 블록이 있습니다.")
        if not translated_text:
            raise ValueError("review 파일에 비어 있는 [[번역문]] 블록이 있습니다.")

        blocks.append((source_text, translated_text))
        position = match.end()

    if review_text[position:].strip():
        raise ValueError("review 파일의 {{원문}} [[번역문]] 블록 밖에 텍스트가 있습니다.")
    if not blocks:
        raise ValueError("review 파일에서 {{원문}} [[번역문]] 블록을 찾지 못했습니다.")

    return blocks


def _validate_review_structure(original_text: str, edited_text: str) -> list[str]:
    original_blocks = _parse_review_blocks(original_text)
    edited_blocks = _parse_review_blocks(edited_text)

    original_sources = [source for source, _ in original_blocks]
    edited_sources = [source for source, _ in edited_blocks]
    if edited_sources != original_sources:
        raise ValueError("review 파일의 {{원문}} 블록 구조가 변경되었습니다.")

    return [translation for _, translation in edited_blocks]


def _get_final_title(final_path: Path) -> str:
    if final_path.is_file():
        try:
            return parse_source_file(final_path).title
        except Exception:
            pass
    return final_path.stem


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


def _save_final_translation_from_review(review_file: Path, original_review_text: str | None = None) -> Path:
    review_text = review_file.read_text(encoding="utf-8")
    translated_chunks = (
        _validate_review_structure(original_review_text, review_text)
        if original_review_text is not None
        else [translation for _, translation in _parse_review_blocks(review_text)]
    )

    final_path = _build_final_translation_path(review_file)
    title = _get_final_title(final_path)
    atomic_write_text(final_path, build_translated_document(title, translated_chunks))
    return final_path


def _confirm_apply_all_review_files(review_files: list[Path]) -> bool:
    print(f"모든 review 파일 {len(review_files)}개를 최종 결과에 적용하시겠습니까? (y/n)")
    return input("").strip().lower() == "y"


def _save_all_final_translations_from_reviews(review_files: list[Path]) -> tuple[list[Path], list[tuple[Path, Exception]]]:
    saved_paths: list[Path] = []
    failed_files: list[tuple[Path, Exception]] = []

    for review_file in review_files:
        try:
            final_path = _save_final_translation_from_review(review_file)
        except Exception as exc:
            failed_files.append((review_file, exc))
            log_runtime_event(f"review batch save failed | review={review_file} | error={exc!r}")
            continue

        saved_paths.append(final_path)
        log_runtime_event(f"review batch saved final translation | review={review_file} | output={final_path}")

    return saved_paths, failed_files


def _select_review_novel(output_root: Path, status_message: str | None) -> tuple[Path | None, str | None]:
    novel_dirs = find_translated_novels(output_root)
    if not novel_dirs:
        render_review_selection_screen(
            output_root=output_root,
            novel_dirs=[],
            status_message=f"[WARN] 번역 폴더가 없습니다: {output_root}",
        )
        wait_for_enter()
        return None, status_message

    render_review_selection_screen(output_root=output_root, novel_dirs=novel_dirs, status_message=status_message)
    raw = input("").strip()
    command = parse_command(raw)
    if command == "back":
        return None, "__BACK__"

    status_message = validate_menu_number(raw, len(novel_dirs))
    if status_message is not None:
        return None, status_message

    return novel_dirs[int(raw) - 1], None


def main() -> int:
    settings = get_runtime_settings()
    output_root = settings.output_root
    status_message: str | None = None

    while True:
        selected_novel, status_message = _select_review_novel(output_root, status_message)
        if status_message == "__BACK__":
            return 0
        if selected_novel is None:
            continue

        file_status: str | None = None
        while True:
            review_files = _find_review_files(selected_novel)
            if not review_files:
                status_message = f"[WARN] review 파일이 없습니다: {selected_novel / REVIEW_DIR_NAME}"
                break

            render_review_file_selection_screen(
                novel_dir=selected_novel,
                review_files=review_files,
                status_message=file_status,
            )
            raw = input("").strip()
            command = parse_command(raw)
            if command == "back":
                status_message = None
                break

            if raw == "-":
                if not _confirm_apply_all_review_files(review_files):
                    file_status = "[INFO] 일괄 적용을 취소했습니다."
                    continue

                saved_paths, failed_files = _save_all_final_translations_from_reviews(review_files)
                if failed_files:
                    failed_names = ", ".join(path.name for path, _ in failed_files[:3])
                    if len(failed_files) > 3:
                        failed_names += f" 외 {len(failed_files) - 3}개"
                    file_status = (
                        f"[WARN] 일괄 적용 완료: 성공 {len(saved_paths)}개, 실패 {len(failed_files)}개 "
                        f"({failed_names})"
                    )
                    continue

                file_status = f"[INFO] 모든 review 파일을 최종 결과에 적용했습니다: {len(saved_paths)}개"
                continue

            file_status = validate_menu_number(raw, len(review_files))
            if file_status is not None:
                continue

            review_file = review_files[int(raw) - 1]
            original_review_text = review_file.read_text(encoding="utf-8")
            _open_in_editor(review_file)
            try:
                final_path = _save_final_translation_from_review(review_file, original_review_text)
            except Exception as exc:
                review_file.write_text(original_review_text, encoding="utf-8")
                log_runtime_event(f"review save failed | review={review_file} | error={exc!r}")
                file_status = f"[ERROR] 검수 구조가 올바르지 않아 적용하지 않았습니다. review 파일을 원복했습니다: {exc}"
                continue

            log_runtime_event(f"review saved final translation | review={review_file} | output={final_path}")
            file_status = f"[INFO] 최종 번역본을 저장했습니다: {final_path.name}"
