from __future__ import annotations

from pathlib import Path

from app.config import get_env_settings_items, get_runtime_settings, update_env_setting
from app.setup import run_model_download_setup
from app.translation import TranslationConfig
from app.ui import (
    parse_command,
    prompt_env_setting_value,
    prompt_env_settings_menu,
    prompt_settings_menu,
    render_settings_menu,
    render_translation_selection_screen,
)
from app.utils import find_chapter_files, find_source_novels, parse_chapter_selection


POSITIVE_INT_KEYS = {"MAX_CHARS", "TIMEOUT", "N_PREDICT", "CTX_SIZE", "STARTUP_TIMEOUT"}
UNIT_FLOAT_KEYS = {"TOP_P"}


def validate_env_setting_value(key: str, new_value: str) -> str | None:
    if "TEMPERATURE" in key or key in UNIT_FLOAT_KEYS:
        try:
            numeric_value = float(new_value)
        except ValueError:
            return f"[ERROR] {key} 값은 숫자로 입력해 주세요."

        if not 0.0 <= numeric_value <= 1.0:
            return f"[ERROR] {key} 값은 0.0 ~ 1.0 범위여야 합니다."

    if key in POSITIVE_INT_KEYS:
        try:
            int_value = int(new_value)
        except ValueError:
            return f"[ERROR] {key} 값은 정수로 입력해 주세요."

        if int_value <= 0:
            return f"[ERROR] {key} 값은 1 이상이어야 합니다."

    if key == "REFINE_ENABLED" and new_value.lower() not in {"on", "off"}:
        return "[ERROR] REFINE_ENABLED는 'on' 또는 'off'로 설정해야 합니다."

    return None


def validate_menu_number(choice: str, item_count: int) -> str | None:
    if not choice.isdigit():
        return "[ERROR] 목록 번호를 입력해 주세요."

    selected_index = int(choice) - 1
    if not 0 <= selected_index < item_count:
        return "[ERROR] 목록에 있는 번호를 입력해 주세요."

    return None


def parse_positive_float_input(raw: str, *, default: float) -> tuple[float | None, str | None]:
    if not raw:
        return default, None

    try:
        value = float(raw)
    except ValueError:
        return None, "[ERROR] 숫자로 입력해 주세요."

    if value <= 0:
        return None, "[ERROR] 0보다 큰 값을 입력해 주세요."

    return value, None


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

        return key, new_value


def run_env_settings_menu() -> str | None:
    status_message = None

    while True:
        items = get_env_settings_items()
        choice = prompt_env_settings_menu(items, status_message)

        if choice == "0":
            return status_message

        status_message = validate_menu_number(choice, len(items))
        if status_message is not None:
            continue

        key, current_value = items[int(choice) - 1]
        key, new_value = run_env_settings_input(key, current_value)
        if new_value is None:
            status_message = "[INFO] 값 변경을 취소했습니다."
            continue

        update_env_setting(key, new_value)
        status_message = f"[INFO] {key} 값을 '{new_value}'로 변경했습니다."


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
            status_message = run_model_download_setup(force_prompt=True)
            continue

        status_message = "[ERROR] 잘못된 입력입니다."


def prompt_for_missing_paths(config: TranslationConfig) -> TranslationConfig:
    runtime_settings = get_runtime_settings()

    if config.server_executable is None:
        raw = input(f"llama-server 실행 파일 경로 (기본: {runtime_settings.llama_server_path}): ").strip().strip('"')
        config.server_executable = Path(raw) if raw else None

    if config.model_path is None:
        raw = input(f"GGUF 모델 파일 경로 (기본: {runtime_settings.llama_model_path}): ").strip().strip('"')
        config.model_path = Path(raw) if raw else None

    if config.glossary_path is None:
        raw = input(f"glossary.json 경로 (기본: {runtime_settings.glossary_path}): ").strip().strip('"')
        config.glossary_path = Path(raw) if raw else runtime_settings.glossary_path

    return config


def prompt_for_source_files_with_ui(source_root: Path) -> list[Path]:
    novel_dirs = find_source_novels(source_root)
    if not novel_dirs:
        raise ValueError(f"No source novel folders found: {source_root}")

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
            status_message = "[ERROR] 3 또는 1~5 형식으로 입력해 주세요."
            continue

        start_number, end_number = selection
        chapter_files_by_number = {int(path.stem): path for path in chapter_files}
        missing_numbers = [number for number in range(start_number, end_number + 1) if number not in chapter_files_by_number]
        if missing_numbers:
            missing_names = ", ".join(f"{number:04d}.txt" for number in missing_numbers)
            status_message = f"[ERROR] 없는 파일이 있습니다: {missing_names}"
            continue

        return [chapter_files_by_number[number] for number in range(start_number, end_number + 1)]
