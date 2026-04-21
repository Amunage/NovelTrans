from __future__ import annotations

from pathlib import Path

from app.config import (
    get_env_settings_items,
    get_runtime_settings,
    log_runtime_event,
    reset_env_settings_to_defaults,
    update_env_setting,
)
from app.setup import run_model_download_setup
from app.translation import TranslationConfig, build_output_path
from app.ui import (
    format_env_setting_value,
    parse_command,
    prompt_env_reset_confirmation,
    prompt_env_setting_value,
    prompt_env_settings_menu,
    prompt_missing_path,
    prompt_settings_menu,
    render_settings_menu,
    render_translation_selection_screen,
)
from app.utils import find_chapter_files, find_source_novels, parse_chapter_selection


POSITIVE_INT_KEYS = {"MAX_CHARS", "TIMEOUT", "N_PREDICT", "CTX_SIZE", "STARTUP_TIMEOUT"}
OPTIONAL_POSITIVE_INT_KEYS = {"GPU_LAYERS", "THREADS"}
UNIT_FLOAT_KEYS = {"TOP_P"}


def validate_env_setting_value(key: str, new_value: str) -> str | None:
    normalized_value = new_value.strip()
    if key in OPTIONAL_POSITIVE_INT_KEYS and normalized_value.lower() == "auto":
        return None

    if "TEMPERATURE" in key or key in UNIT_FLOAT_KEYS:
        try:
            numeric_value = float(normalized_value)
        except ValueError:
            return f"[ERROR] {key} 값을 숫자로 입력해 주세요."

        if not 0.0 <= numeric_value <= 1.0:
            return f"[ERROR] {key} 값은 0.0 ~ 1.0 범위여야 합니다."

    if key in POSITIVE_INT_KEYS or key in OPTIONAL_POSITIVE_INT_KEYS:
        try:
            int_value = int(normalized_value)
        except ValueError:
            return f"[ERROR] {key} 값을 정수로 입력해 주세요."

        if int_value <= 0:
            return f"[ERROR] {key} 값은 1 이상이어야 합니다."

    if key == "REFINE_ENABLED" and new_value.lower() not in {"on", "off"}:
        return "[ERROR] REFINE_ENABLED는 'on' 또는 'off'로 설정해야 합니다."

    return None


def normalize_env_setting_value(key: str, new_value: str) -> str:
    normalized_value = new_value.strip()
    if key in OPTIONAL_POSITIVE_INT_KEYS and normalized_value.lower() == "auto":
        return ""
    return normalized_value


def validate_menu_number(choice: str, item_count: int) -> str | None:
    if not choice.isdigit():
        return "[ERROR] 목록 번호를 입력해 주세요."

    selected_index = int(choice) - 1
    if not 0 <= selected_index < item_count:
        return "[ERROR] 목록에 있는 번호를 입력해 주세요."

    return None


def run_env_settings_input(key: str, value: str) -> tuple[str, str | None]:
    status_message = None

    while True:
        items = get_env_settings_items()
        new_value = prompt_env_setting_value(key, value, items, status_message)

        if new_value == "":
            return key, None

        status_message = validate_env_setting_value(key, new_value)
        if status_message is not None:
            continue

        return key, normalize_env_setting_value(key, new_value)


def run_env_settings_menu() -> str | None:
    status_message = None

    while True:
        items = get_env_settings_items()
        choice = prompt_env_settings_menu(items, status_message)

        if choice == "0":
            return status_message

        if choice.lower() == "-":
            status_message = "[WARNING] 환경설정을 초기화하시겠습니까? (y/n)"
            items = get_env_settings_items()
            confirm = prompt_env_reset_confirmation(items, status_message)
            if confirm != "y":
                status_message = "[INFO] 환경설정 초기화를 취소했습니다."
                continue

            reset_env_settings_to_defaults()
            status_message = "[INFO] 환경설정을 초기값으로 되돌렸습니다."
            continue

        status_message = validate_menu_number(choice, len(items))
        if status_message is not None:
            continue

        key, current_value = items[int(choice) - 1]
        key, new_value = run_env_settings_input(key, current_value)
        if new_value is None:
            status_message = "[INFO] 값 변경을 취소했습니다."
            continue

        update_env_setting(key, new_value)
        display_value = format_env_setting_value(key, new_value)
        log_runtime_event(f"env setting updated | key={key} | value={display_value}")
        status_message = f"[INFO] {key} 값을 '{display_value}'로 변경했습니다."


def run_settings_menu() -> str | None:
    status_message = None

    while True:
        choice = prompt_settings_menu(status_message)

        if choice == "0":
            return None

        if choice == "1":
            status_message = run_env_settings_menu() or "[INFO] 환경설정을 확인했습니다."
            continue

        if choice == "2":
            render_settings_menu("[INFO] 컴퓨터 사양을 분석하는 중입니다...")
            try:
                log_runtime_event("settings menu | manual model download requested")
                status_message = run_model_download_setup(force_prompt=True)
            except Exception as exc:
                log_runtime_event(f"settings menu | model download error={exc!r}")
                status_message = f"[ERROR] 모델 다운로드 중 오류가 발생했습니다: {exc}"
            continue

        status_message = "[ERROR] 잘못된 입력입니다."


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


def prompt_for_source_files_with_ui(source_root: Path) -> list[Path]:
    novel_dirs = find_source_novels(source_root)
    if not novel_dirs:
        raise ValueError(f"No source novel folders found: {source_root}")

    runtime_settings = get_runtime_settings()
    step = "novel"
    status_message = None
    selected_novel: Path | None = None

    while True:
        if step == "novel":
            render_translation_selection_screen(
                step="novel",
                source_root=source_root,
                novel_dirs=novel_dirs,
                status_message=status_message,
            )
            raw = input("").strip()
            command = parse_command(raw)

            if command in {"main", "back"}:
                return []

            status_message = validate_menu_number(raw, len(novel_dirs))
            if status_message is not None:
                continue

            selected_novel = novel_dirs[int(raw) - 1]
            step = "chapter"
            status_message = None
            continue

        assert selected_novel is not None
        chapter_files = find_chapter_files(selected_novel)
        if not chapter_files:
            raise ValueError(f"No chapter files found in: {selected_novel}")

        render_translation_selection_screen(
            step="chapter",
            source_root=source_root,
            novel_dirs=novel_dirs,
            selected_novel=selected_novel,
            chapter_files=chapter_files,
            last_translated_label=_find_last_translated_label(chapter_files, runtime_settings.output_root),
            status_message=status_message,
        )
        raw = input("").strip()
        command = parse_command(raw)

        if command == "main":
            return []
        if command == "back":
            step = "novel"
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

        return chapter_files[start_index - 1 : end_index]
