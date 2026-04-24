from __future__ import annotations

import os
import unicodedata
from pathlib import Path
from typing import Sequence


ChapterLike = tuple[int, str, str]
SETTING_DESCRIPTIONS = {
    "LLAMA_SERVER_PATH": "파일 경로 | llama-server 실행 파일 위치",
    "LLAMA_MODEL_PATH": "파일 경로 | 사용할 GGUF 모델 파일 위치",
    "GLOSSARY_PATH": "파일 경로 | 용어집 JSON 파일 위치",
    "TARGET_LANG": "japanese / chinese | 용어집 후보 추출 기준 언어",
    "SOURCE_PATH": "폴더 경로 | 원문 txt 폴더 위치",
    "OUTPUT_ROOT": "폴더 경로 | 번역 결과 저장 폴더",
    "SERVER_URL": "URL | llama-server 주소, 예: http://127.0.0.1:8080",
    "MAX_CHARS": "정수 | 청크당 최대 글자 수",
    "TIMEOUT": "정수(초) | 모델 응답 대기 시간",
    "DRAFT_TEMPERATURE": "0.0 ~ 1.0 | 낮을수록 보수적, 높을수록 창의적",
    "REFINE_TEMPERATURE": "0.0 ~ 1.0 | 낮을수록 보수적, 높을수록 창의적",
    "REFINE_ENABLED": "on / off | 번역 후 다듬기 사용 여부",
    "TOP_P": "0.0 ~ 1.0 | 낮을수록 좁게 선택, 높을수록 다양하게 선택",
    "N_PREDICT": "정수 | 최대 출력 토큰 수",
    "CTX_SIZE": "정수 | 모델 컨텍스트 크기",
    "GPU_LAYERS": "정수 또는 auto | GPU에 올릴 레이어 수",
    "THREADS": "정수 또는 auto | CPU 스레드 수",
    "STARTUP_TIMEOUT": "정수(초) | 서버 시작 대기 시간",
}
AUTO_DISPLAY_ENV_KEYS = {"GPU_LAYERS", "THREADS"}


def clear_screen() -> None:
    os.system("cls")


def _print_header(title: str) -> None:
    print("=" * 60)
    print(title)
    print("=" * 60)


def _build_progress_bar(current: int, total: int, width: int = 28) -> str:
    safe_total = max(total, 1)
    safe_current = max(0, min(current, safe_total))
    filled = round((safe_current / safe_total) * width)
    return f"[{'#' * filled}{'-' * (width - filled)}]"


def _get_display_width(value: str) -> int:
    width = 0
    for char in value:
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W", "A"} else 1
    return width


def _pad_display(value: str, width: int) -> str:
    padding = max(0, width - _get_display_width(value))
    return value + (" " * padding)


def _format_elapsed_time(elapsed_seconds: int) -> str:
    safe_seconds = max(0, elapsed_seconds)
    hours, remainder = divmod(safe_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def format_env_setting_value(key: str, value: str) -> str:
    if key in AUTO_DISPLAY_ENV_KEYS and value.strip() == "":
        return "auto"
    return value


def render_main_menu(status_message: str | None = None) -> None:
    clear_screen()
    _print_header("메인 메뉴")
    print("원하는 작업 번호를 입력해 주세요.")
    print("-" * 60)
    print("[1] 웹소설 추출")
    print("[2] 텍스트 번역")
    print("[3] 용어집 생성")
    print("[4] 환경 설정")
    print("[5] 시스템 진단")
    print("[=] 종료")
    print("-" * 60)
    print(status_message or "")
    print("-" * 60)


def render_settings_menu(status_message: str | None = None) -> None:
    clear_screen()
    _print_header("환경 설정")
    print("원하는 작업 번호를 입력해 주세요.")
    print("-" * 60)
    print("[1] ENV 관리")
    print("[2] 모델 다운로드")
    print("[3] 업데이트 확인")
    print("[=] 뒤로가기")
    print("-" * 60)
    print(status_message or "")
    print("-" * 60)


def render_diagnostics_screen(
    lines: Sequence[str],
    summary: str,
    status_message: str | None = None,
) -> None:
    clear_screen()
    _print_header("시스템 진단")
    print("Full system check results")
    print("-" * 60)
    for line in lines:
        print(line)
    print("-" * 60)
    print(summary)
    print(status_message or "")
    print("-" * 60)


def render_env_settings_menu(
    items: Sequence[tuple[str, str]],
    status_message: str | None = None,
) -> None:
    clear_screen()
    _print_header("ENV 관리")
    print("원하는 작업 번호를 입력해 주세요.")
    print("-" * 60)
    for index, (key, value) in enumerate(items, start=1):
        print(f"[{index}] {key} = {format_env_setting_value(key, value)}")
    print("-" * 60)
    print("[-] 초기화")
    print("[=] 뒤로가기")
    print("-" * 60)
    print(status_message or "")
    print("-" * 60)


def render_crawler_screen(
    step: str,
    status_message: str | None = None,
    chapters: Sequence[ChapterLike] | None = None,
) -> None:
    clear_screen()
    _print_header("웹소설 추출")
    print("= 뒤로가기")
    print("-" * 60)

    if step == "url":
        print("소설 메인 URL을 입력해 주세요.")
        print("(예: https://syosetu.org/novel/267236/)")
    elif step == "range" and chapters:
        print(f"발견 챕터: {len(chapters)}개 ({chapters[0][0]}~{chapters[-1][0]})")
        print("추출 범위를 입력해 주세요. (예: 3~15 또는 3-15, 빈값이면 전체)")
    elif step == "delay" and chapters:
        print(f"대상 챕터: {len(chapters)}개 ({chapters[0][0]}~{chapters[-1][0]})")
        print("요청 간격을 입력해 주세요. (빈값이면 기본 1.0초)")

    print("-" * 60)
    print(status_message or "")
    print("-" * 60)


def render_crawler_error_screen(
    url: str,
    error: Exception,
    status_message: str | None = None,
    waiting_for_retry: bool = False,
) -> None:
    clear_screen()
    _print_header("추출 오류")
    print(f"URL: {url}")
    print(f"오류: {error}")
    print("-" * 60)
    if waiting_for_retry:
        print("명령: = 입력 시 이전 오류 메뉴로")
    else:
        print("명령: 숫자 1~4 입력")
    print("-" * 60)
    print(status_message or "")
    print("-" * 60)

    if not waiting_for_retry:
        print("  [1] 이 챕터 건너뛰고 계속 진행")
        print("  [2] 이 챕터 다시 시도")
        print("  [3] 이후 오류는 모두 자동 건너뛰기")
        print("  [4] 추출 중단")


def render_wait_screen(wait_time: float) -> None:
    clear_screen()
    print(f"{wait_time}초 대기 중...")


def render_crawl_progress_screen(
    *,
    chapters: Sequence[ChapterLike],
    current_index: int,
    total: int,
    current_title: str,
    output_path: Path,
    status_message: str | None = None,
    failed_count: int = 0,
) -> None:
    clear_screen()
    _print_header("웹소설 추출")
    if chapters:
        print(f"대상 챕터: {len(chapters)}개 ({chapters[0][0]}~{chapters[-1][0]})")
    print("-" * 60)
    print(f"진행률: {_build_progress_bar(current_index, total)} {current_index}/{total}")
    print(f"실패: {failed_count}개")
    print(f"현재 작업: {current_title}")
    print(f"출력 경로: {output_path}")
    print("-" * 60)
    print(status_message or "")
    print("-" * 60)


def render_crawl_complete_screen(
    *,
    total: int,
    success_count: int,
    failed_count: int,
    output_path: Path,
    status_message: str | None = None,
) -> None:
    clear_screen()
    _print_header("추출 완료")
    print("엔터를 누르면 돌아갑니다.")
    print("-" * 60)
    print(f"성공: {success_count}/{total}")
    print(f"실패: {failed_count}개")
    print(f"출력 폴더: {output_path}")
    print("-" * 60)
    print(status_message or "")
    print("-" * 60)


def render_translation_selection_screen(
    *,
    step: str,
    source_root: Path,
    novel_dirs: Sequence[Path],
    selected_novel: Path | None = None,
    chapter_files: Sequence[Path] | None = None,
    last_translated_label: str | None = None,
    status_message: str | None = None,
) -> None:
    clear_screen()
    _print_header("텍스트 번역")
    print("= 뒤로가기")
    print("-" * 60)
    print(f"소스 폴더: {source_root}")

    if step == "novel":
        print("번역할 소설 번호를 입력해 주세요.")
        print("-" * 60)
        for index, novel_dir in enumerate(novel_dirs, start=1):
            print(f"[{index}] {novel_dir.name}")
    elif step == "chapter" and selected_novel is not None and chapter_files is not None:
        print("번역할 챕터 번호 또는 범위를 입력해 주세요. (예: 3 또는 1~5, 1-5)")
        print("-" * 60)
        print(f"선택한 소설: {selected_novel.name}")
        print(f"발견 챕터: {len(chapter_files)}개")
        print(f"마지막 번역 지점: {last_translated_label or '없음'}")

    print("-" * 60)
    print(status_message or "")
    print("-" * 60)


def render_translation_progress_screen(
    *,
    file_index: int,
    total_files: int,
    stage: str,
    current: int,
    total: int,
    elapsed_seconds: int = 0,
    source_file: Path,
    title: str,
    output_path: Path,
    status_message: str | None = None,
) -> None:
    clear_screen()
    _print_header("텍스트 번역")
    print(f"단계: {stage}")
    print("-" * 60)

    if stage == "모델 로드":
        print("모델을 불러오는 중...")
    else:
        print(f"전체 진행: {_build_progress_bar(file_index - 1, total_files)} {file_index}/{total_files}")
        print(f"챕터 진행: {_build_progress_bar(current, total)} {current}/{total}")

    print(f"경과 시간: {_format_elapsed_time(elapsed_seconds)}")

    if stage in {"초벌 번역", "다듬기"}:
        print("-" * 60)
        print(f"파일: {source_file.name}")
        print(f"작업 제목: {title}")
        print(f"출력 경로: {output_path}")

    print("-" * 60)
    print(status_message or "")
    print("-" * 60)


def render_translation_complete_screen(
    *,
    total_files: int,
    completed_files: int,
    output_root: Path,
    last_output_path: Path | None = None,
    elapsed_seconds: int = 0,
    status_message: str | None = None,
) -> None:
    clear_screen()
    _print_header("텍스트 번역")
    print("엔터를 누르면 돌아갑니다.")
    print("-" * 60)
    print(f"완료 파일: {completed_files}/{total_files}")
    print(f"경과 시간: {_format_elapsed_time(elapsed_seconds)}")
    print(f"출력 폴더: {output_root}")
    if last_output_path is not None:
        print(f"결과 파일: {last_output_path}")
    print("-" * 60)
    print(status_message or "")
    print("-" * 60)


def render_glossary_selection_screen(
    *,
    source_root: Path,
    novel_dirs: Sequence[Path],
    status_message: str | None = None,
) -> None:
    clear_screen()
    _print_header("용어집 생성")
    print("= 뒤로가기")
    print("-" * 60)
    print(f"소설 폴더: {source_root}")
    print("탐색할 소설 번호를 입력해 주세요.")
    print("-" * 60)
    for index, novel_dir in enumerate(novel_dirs, start=1):
        print(f"[{index}] {novel_dir.name}")
    print("-" * 60)
    print(status_message or "")
    print("-" * 60)


def render_glossary_candidate_progress_screen(status_message: str | None = None) -> None:
    clear_screen()
    _print_header("용어집 생성")
    print("-" * 60)
    print(status_message or "")
    print("-" * 60)


def render_glossary_refine_progress_screen(
    *,
    novel_name: str,
    batch_index: int,
    total_batches: int,
    accepted_count: int,
    status_message: str | None = None,
) -> None:
    clear_screen()
    _print_header("용어집 생성")
    print(f"소설: {novel_name}")
    print(f"용어 정제: {_build_progress_bar(batch_index, total_batches)} {batch_index}/{max(total_batches, 1)}")
    print(f"확정 용어: {accepted_count}")
    print("-" * 60)
    print(status_message or "")
    print("-" * 60)


def render_glossary_complete_screen(
    *,
    output_path: Path,
    candidate_count: int,
    elapsed_seconds: int,
    status_message: str | None = None,
) -> None:
    clear_screen()
    _print_header("용어집 생성 완료")
    print("엔터를 누르면 돌아갑니다.")
    print("-" * 60)
    print(f"저장 파일: {output_path}")
    print(f"용어 개수: {candidate_count}")
    print(f"경과 시간: {_format_elapsed_time(elapsed_seconds)}")
    print("-" * 60)
    print(status_message or "")
    print("-" * 60)


def render_download_progress_screen(
    *,
    title: str,
    message: str,
    item_label: str,
    item_name: str,
    destination_path: str,
    percent: int,
    speed_mbps: float | None = None,
) -> None:
    clear_screen()
    _print_header(title)
    print(message)
    print("-" * 60)
    print(f"{item_label}: {item_name}")
    print(f"저장 위치: {destination_path}")
    speed_text = f"{speed_mbps:.1f} MB/s" if speed_mbps is not None else "속도 측정 중..."
    print(f"다운로드: {_build_progress_bar(percent, 100)} {percent}% ({speed_text})")
    print("-" * 60)
    print("Ctrl+C: 다운로드 취소")
    print("-" * 60)


def render_model_download_menu(
    system_specs: dict[str, object],
    model_options: list[dict[str, object]],
    recommended_index: int,
) -> None:
    clear_screen()
    _print_header("모델 다운로드")
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
    print(" [-] 모델 사이트 열기")
    print(" [=] 뒤로가기")
    print("-" * 60)


def format_system_specs(system_specs: dict[str, object]) -> str:
    gpu_name = system_specs.get("gpu_name") or "없음"
    gpu_vram_gb = system_specs.get("gpu_vram_gb")
    gpu_text = f"{gpu_name} ({gpu_vram_gb:.1f} GB VRAM)" if isinstance(gpu_vram_gb, float) else str(gpu_name)
    return (
        f"감지된 사양: RAM {float(system_specs['ram_gb']):.1f} GB | "
        f"GPU {gpu_text} | "
        f"디스크 여유 {float(system_specs['disk_free_gb']):.1f} GB | "
        f"CPU 스레드 {int(system_specs['cpu_threads'])}"
    )


def build_model_option_row(option: dict[str, object]) -> dict[str, str]:
    size_bytes = option.get("size_bytes")
    size_gb = size_bytes / (1024**3) if isinstance(size_bytes, int) else None
    size_text = f"{size_gb:.1f} GB" if size_gb is not None else "크기 미확인"
    min_vram_gb = option.get("min_vram_gb")
    vram_text = f"{int(min_vram_gb)} GB+" if isinstance(min_vram_gb, (int, float)) else "정보 없음"
    return {
        "label": str(option["label"]),
        "filename": str(option["filename"]),
        "size_text": size_text,
        "vram_text": vram_text,
        "summary": str(option["summary"]),
    }
