from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence

if __package__ in {None, ""}:
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from app.settings.config import DATA_ROOT, get_runtime_settings
from app.translation.engine import TranslationConfig, build_output_path
from app.ui.render import (
    SETTING_DESCRIPTIONS,
    format_env_setting_value,
    render_crawler_error_screen,
    render_crawler_screen,
    render_env_settings_menu,
    render_glossary_min_term_count_screen,
    render_glossary_selection_screen,
    render_main_menu,
    render_settings_menu,
    render_translation_selection_screen,
)
from app.utils import find_chapter_files, find_source_novels, parse_chapter_selection
from app.ui.validators import validate_menu_number


COMMAND_ALIASES = {
    "back": {"="},
}


def parse_command(value: str) -> str | None:
    normalized = value.strip().lower()
    for command, aliases in COMMAND_ALIASES.items():
        if normalized in aliases:
            return command
    return None


def prompt_main_menu(status_message: str | None = None) -> str:
    render_main_menu(status_message)
    return input("").strip()


def prompt_settings_menu(status_message: str | None = None) -> str:
    render_settings_menu(status_message)
    return input("").strip()


def prompt_env_settings_menu(
    items: Sequence[tuple[str, str]],
    status_message: str | None = None,
) -> str:
    render_env_settings_menu(items, status_message)
    return input("").strip()


def prompt_env_reset_confirmation(
    items: Sequence[tuple[str, str]],
    status_message: str,
) -> str:
    render_env_settings_menu(items, status_message)
    return input("").strip().lower()


def build_env_setting_status_message(key: str, value: str) -> str:
    description = SETTING_DESCRIPTIONS.get(key, "")
    status_message = f"{key} 현재값 {format_env_setting_value(key, value)}"
    if description:
        status_message = f"{status_message} ({description})"
    return status_message


def prompt_env_setting_value(
    key: str,
    value: str,
    items: Sequence[tuple[str, str]],
    status_message: str | None = None,
) -> str:
    render_env_settings_menu(items, status_message or build_env_setting_status_message(key, value))
    return input("새 값 입력 (빈값이면 취소): ").strip()


def prompt_missing_path(label: str, default_path: Path) -> str:
    return input(f"{label} (기본: {default_path}): ").strip().strip('"')


def prompt_crawler_screen(
    step: str,
    status_message: str | None = None,
    chapters: Sequence[tuple[int, str, str]] | None = None,
) -> str:
    render_crawler_screen(step, status_message, chapters)
    return input("").strip()


def prompt_crawler_error_choice(
    url: str,
    error: Exception,
    status_message: str | None = None,
) -> str:
    render_crawler_error_screen(url, error, status_message=status_message)
    return input("선택 (1/2/3/4): ").strip()


def prompt_crawler_retry_wait(
    url: str,
    error: Exception,
    status_message: str | None = None,
) -> str:
    render_crawler_error_screen(url, error, status_message=status_message, waiting_for_retry=True)
    return input("대기 시간(초, 기본 5, 뒤로가기 =): ").strip()


def prompt_glossary_novel_choice(
    *,
    source_root: Path,
    novel_dirs: Sequence[Path],
    target_lang: str | None = None,
    status_message: str | None = None,
) -> str:
    render_glossary_selection_screen(
        source_root=source_root,
        novel_dirs=novel_dirs,
        target_lang=target_lang,
        status_message=status_message,
    )
    return input("").strip()


def prompt_glossary_min_term_count(
    *,
    default_count: int,
    status_message: str | None = None,
) -> str:
    render_glossary_min_term_count_screen(
        default_count=default_count,
        status_message=status_message,
    )
    return input("최소 출현 횟수: ").strip()


def wait_for_enter() -> None:
    input("")


def prompt_for_missing_paths(config: TranslationConfig) -> TranslationConfig:
    runtime_settings = get_runtime_settings()

    if config.server_executable is None:
        raw = prompt_missing_path("llama-server 실행 파일 경로", runtime_settings.llama_server_path)
        config.server_executable = Path(raw) if raw else None

    if config.model_path is None:
        raw = prompt_missing_path("GGUF 모델 파일 경로", runtime_settings.llama_model_path)
        config.model_path = Path(raw) if raw else None

    if config.glossary_path is None:
        raw = prompt_missing_path("glossary.json 경로", runtime_settings.glossary_path)
        config.glossary_path = Path(raw) if raw else runtime_settings.glossary_path

    return config


def _find_last_translated_label(chapter_files: list[Path], output_root: Path) -> str | None:
    last_match: tuple[int, Path] | None = None

    for index, chapter_file in enumerate(chapter_files, start=1):
        output_path = build_output_path(chapter_file, output_root)
        if output_path.is_file():
            last_match = (index, chapter_file)

    if last_match is None:
        return None

    chapter_index, chapter_file = last_match
    return f"[{chapter_index}] {chapter_file.name}"


def _find_glossary_files() -> list[Path]:
    glossary_dir = DATA_ROOT / "glossary"
    if not glossary_dir.is_dir():
        return []
    return sorted(glossary_dir.glob("*.json"), key=lambda path: (path.name != "default.json", path.name.lower()))


def _add_glossary_file(glossary_files: list[Path], glossary_path: Path) -> list[Path]:
    if glossary_path in glossary_files:
        return glossary_files
    if not glossary_path.is_file():
        return glossary_files
    return [*glossary_files, glossary_path]


def _get_default_glossary_for_novel(
    selected_novel: Path,
    glossary_files: Sequence[Path],
    fallback_glossary: Path,
) -> Path:
    glossary_by_name = {path.name: path for path in glossary_files}
    return glossary_by_name.get(f"{selected_novel.name}.json") or fallback_glossary


def prompt_for_source_files_with_ui(source_root: Path) -> tuple[list[Path], Path | None]:
    novel_dirs = find_source_novels(source_root)
    if not novel_dirs:
        raise ValueError(f"No source novel folders found: {source_root}")

    runtime_settings = get_runtime_settings()
    step = "novel"
    status_message = None
    selected_novel: Path | None = None
    selected_glossary: Path | None = None

    while True:
        if step == "novel":
            render_translation_selection_screen(
                step="novel",
                source_root=source_root,
                novel_dirs=novel_dirs,
                target_lang=runtime_settings.target_lang,
                status_message=status_message,
            )
            raw = input("").strip()
            command = parse_command(raw)

            if command == "back":
                return [], None

            status_message = validate_menu_number(raw, len(novel_dirs))
            if status_message is not None:
                continue

            selected_novel = novel_dirs[int(raw) - 1]
            step = "glossary"
            status_message = None
            continue

        assert selected_novel is not None
        if step == "glossary":
            glossary_files = _add_glossary_file(_find_glossary_files(), runtime_settings.glossary_path)
            if not glossary_files:
                selected_glossary = runtime_settings.glossary_path
                step = "chapter"
                status_message = f"[WARN] 용어집 파일이 없어 기본 경로를 사용합니다: {selected_glossary}"
                continue

            default_glossary = _get_default_glossary_for_novel(
                selected_novel,
                glossary_files,
                runtime_settings.glossary_path,
            )
            render_translation_selection_screen(
                step="glossary",
                source_root=source_root,
                novel_dirs=novel_dirs,
                target_lang=runtime_settings.target_lang,
                selected_novel=selected_novel,
                glossary_files=glossary_files,
                default_glossary=default_glossary,
                status_message=status_message,
            )
            raw = input("").strip()
            command = parse_command(raw)

            if command == "back":
                step = "novel"
                status_message = None
                continue

            if raw == "":
                selected_glossary = default_glossary
                step = "chapter"
                status_message = None
                continue

            status_message = validate_menu_number(raw, len(glossary_files))
            if status_message is not None:
                continue

            selected_glossary = glossary_files[int(raw) - 1]
            step = "chapter"
            status_message = None
            continue

        if selected_glossary is None:
            selected_glossary = runtime_settings.glossary_path

        chapter_files = find_chapter_files(selected_novel)
        if not chapter_files:
            raise ValueError(f"No chapter files found in: {selected_novel}")

        render_translation_selection_screen(
            step="chapter",
            source_root=source_root,
            novel_dirs=novel_dirs,
            target_lang=runtime_settings.target_lang,
            selected_novel=selected_novel,
            selected_glossary=selected_glossary,
            chapter_files=chapter_files,
            last_translated_label=_find_last_translated_label(chapter_files, runtime_settings.output_root),
            status_message=status_message,
        )
        raw = input("").strip()
        command = parse_command(raw)

        if command == "back":
            step = "glossary"
            status_message = None
            continue

        selection = parse_chapter_selection(raw)
        if selection is None:
            status_message = "[ERROR] 3 또는 1~5, 1-5 형식으로 입력해 주세요."
            continue

        start_index, end_index = selection
        if start_index < 1 or end_index > len(chapter_files):
            status_message = f"[ERROR] 1~{len(chapter_files)} 범위 내로 입력해 주세요."
            continue

        return chapter_files[start_index - 1 : end_index], selected_glossary
