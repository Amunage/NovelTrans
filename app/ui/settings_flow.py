from __future__ import annotations

import webbrowser
from pathlib import Path

from app.settings.config import get_env_settings_items, reset_env_settings_to_defaults, update_env_setting
from app.settings.logging import log_runtime_event
from app.ui.control import (
    parse_command,
    prompt_env_reset_confirmation,
    prompt_env_setting_value,
    prompt_env_settings_menu,
    prompt_settings_menu,
)
from app.ui.render import (
    build_model_option_row,
    clear_screen,
    format_env_setting_value,
    format_system_specs,
    render_settings_menu,
    _get_display_width,
    _pad_display,
    _print_header,
)
from app.ui.validators import normalize_env_setting_value, validate_env_setting_value, validate_menu_number


def prompt_for_model_download(
    system_specs: dict[str, object],
    model_options: list[dict[str, object]],
    recommended_index: int,
) -> dict[str, object] | None:
    clear_screen()
    _print_header("Gemma 4 모델 자동 다운로드")
    print("다운로드할 모델을 선택해 주세요.")
    print("-" * 60)
    print(format_system_specs(system_specs))
    print("-" * 60)
    print("선택 가능한 모델:")
    rows = [build_model_option_row(option) for option in model_options]
    label_width = max([_get_display_width("모델"), *(_get_display_width(row["label"]) for row in rows)], default=0)
    filename_width = max([_get_display_width("파일명"), *(_get_display_width(row["filename"]) for row in rows)], default=0)
    size_width = max([_get_display_width("크기"), *(_get_display_width(row["size_text"]) for row in rows)], default=0)
    vram_width = max([_get_display_width("권장 VRAM"), *(_get_display_width(row["vram_text"]) for row in rows)], default=0)
    header = (
        f"     {_pad_display('모델', label_width)} | "
        f"{_pad_display('파일명', filename_width)} | "
        f"{_pad_display('크기', size_width)} | "
        f"{_pad_display('권장 VRAM', vram_width)} | 설명"
    )
    print(header)
    print(f"     {'-' * label_width}-+-{'-' * filename_width}-+-{'-' * size_width}-+-{'-' * vram_width}-+ {'-' * 20}")
    for index, option in enumerate(model_options, start=1):
        prefix = "*" if index - 1 == recommended_index else " "
        row = rows[index - 1]
        print(
            f"{prefix}[{index}] "
            f"{_pad_display(row['label'], label_width)} | "
            f"{_pad_display(row['filename'], filename_width)} | "
            f"{_pad_display(row['size_text'], size_width)} | "
            f"{_pad_display(row['vram_text'], vram_width)} | "
            f"{row['summary']}"
        )
    print("-" * 60)
    print(" [-] Hugging Face 모델 페이지 열기")
    print(" [=] 뒤로가기")
    print("-" * 60)

    while True:
        choice = input("").strip().lower()
        if choice == "" or choice in {"0", "="}:
            return None
        if choice == "-":
            webbrowser.open("https://huggingface.co/unsloth/gemma-4-26B-A4B-it-GGUF/tree/main")
            webbrowser.open("https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/tree/main")
            print("[INFO] Hugging Face 모델 페이지를 브라우저로 열었습니다.")
            continue
        if choice.isdigit():
            selected_index = int(choice) - 1
            if 0 <= selected_index < len(model_options):
                return model_options[selected_index]
        print("[ERROR] 목록 번호를 입력해 주세요.")


def prompt_llama_runtime_install(server_path: Path) -> bool:
    clear_screen()
    _print_header("llama.cpp 런타임 설치")
    print("llama-server 실행 파일이 없습니다.")
    print(f"필요 파일: {server_path}")
    print("-" * 60)
    print("지금 llama.cpp 런타임을 설치하시겠습니까? (y/n)")
    print("-" * 60)
    return input("").strip().lower() == "y"


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
        command = parse_command(choice)

        if choice == "0" or command == "back":
            return status_message

        if choice == "-":
            status_message = "[WARNING] 환경설정을 초기화하시겠습니까? (y/n)"
            confirm = prompt_env_reset_confirmation(items, status_message)
            if confirm != "y":
                status_message = "[INFO] 환경설정 초기화를 취소했습니다."
                continue

            reset_env_settings_to_defaults()
            status_message = "[INFO] 환경설정이 초기값으로 돌아왔습니다."
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
        command = parse_command(choice)

        if choice == "0" or command == "back":
            return None

        if choice == "1":
            status_message = run_env_settings_menu() or "[INFO] 환경설정을 확인했습니다."
            continue

        if choice == "2":
            render_settings_menu("[INFO] 컴퓨터 사양을 분석하는 중입니다...")
            try:
                from app.settings.setup import run_model_download_setup

                log_runtime_event("settings menu | manual model download requested")
                status_message = run_model_download_setup(force_prompt=True)
            except Exception as exc:
                log_runtime_event(f"settings menu | model download error={exc!r}")
                status_message = f"[ERROR] 모델 다운로드 중 오류가 발생했습니다: {exc}"
            continue

        status_message = "[ERROR] 잘못된 입력입니다."


__all__ = [
    "prompt_for_model_download",
    "prompt_llama_runtime_install",
    "run_env_settings_menu",
    "run_settings_menu",
]