from __future__ import annotations

import os
from pathlib import Path
from typing import Sequence


ChapterLike = tuple[int, str, str]
COMMAND_ALIASES = {
    "back": {"/b"},
    "main": {"/m"},
    "exit": {"/e"},
}


def parse_command(value: str) -> str | None:
    normalized = value.strip().lower()
    for command, aliases in COMMAND_ALIASES.items():
        if normalized in aliases:
            return command
    return None


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


def render_main_menu(status_message: str | None = None) -> None:
    clear_screen()
    _print_header("메인 메뉴")
    print("1. 크롤링")
    print("2. 번역")
    print("3. 종료")
    print("-" * 60)
    print("원하는 작업 번호를 입력해 주세요.")
    print()
    print(status_message or "")
    print("-" * 60)


def render_crawler_screen(
    step: str,
    status_message: str | None = None,
    chapters: Sequence[ChapterLike] | None = None,
) -> None:
    clear_screen()
    _print_header("크롤러")
    print("/b 뒤로가기")
    print("/m 메인메뉴")
    print("/e 종료")
    print("-" * 60)

    if step == "url":
        print("소설 메인 URL을 입력해 주세요.")
        print("(예: https://syosetu.org/novel/267236/)")
    elif step == "range" and chapters:
        print(f"발견 챕터: {len(chapters)}개 ({chapters[0][0]}~{chapters[-1][0]})")
        print("크롤링 범위를 입력해 주세요. (예: 3~15, 미입력 시 전체)")
    elif step == "delay" and chapters:
        print(f"대상 챕터: {len(chapters)}개 ({chapters[0][0]}~{chapters[-1][0]})")
        print("요청 간격을 입력해 주세요. (미입력 시 기본=1.0, 빠름=0.5, 안전=2.0)")

    print(status_message or "")
    print("-" * 60)


def render_crawler_error_screen(
    url: str,
    error: Exception,
    status_message: str | None = None,
    waiting_for_retry: bool = False,
) -> None:
    clear_screen()
    _print_header("크롤러 오류")
    print(f"URL: {url}")
    print(f"오류: {error}")
    print("-" * 60)
    print("명령: /b = 이전 오류 메뉴로" if waiting_for_retry else "명령: 숫자 1~4 입력")
    if status_message:
        print("-" * 60)
        print(status_message)
    print("-" * 60)

    if not waiting_for_retry:
        print("  [1] 이 챕터 건너뛰고 계속 진행")
        print("  [2] 이 챕터 재시도(수동)")
        print("  [3] 이후 오류는 모두 자동 건너뛰기")
        print("  [4] 크롤링 중단")


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
    _print_header("크롤러")
    if chapters:
        print(f"대상 챕터: {len(chapters)}개 ({chapters[0][0]}~{chapters[-1][0]})")
    print(f"저장 경로: {output_path}")
    print(f"진행률: {_build_progress_bar(current_index, total)} {current_index}/{total}")
    print(f"실패: {failed_count}개")
    print("-" * 60)
    print(f"현재 작업: {current_title}")
    print(status_message or "")
    print("-" * 60)


def render_crawl_complete_screen(
    *,
    total: int,
    success_count: int,
    failed_count: int,
    output_path: Path,
    combined_path: Path | None = None,
    status_message: str | None = None,
) -> None:
    clear_screen()
    _print_header("크롤러")
    print("[INFO] 크롤링이 완료되었습니다.")
    print("-" * 60)
    print(f"성공: {success_count}/{total}")
    print(f"실패: {failed_count}개")
    print(f"저장 폴더: {output_path}")
    if combined_path is not None:
        print(f"합본 파일: {combined_path}")
    if status_message:
        print("-" * 60)
        print(status_message)
    print("-" * 60)
    print("엔터를 누르면 메인 메뉴로 돌아갑니다.")


def render_translation_selection_screen(
    *,
    step: str,
    source_root: Path,
    novel_dirs: Sequence[Path],
    selected_novel: Path | None = None,
    chapter_files: Sequence[Path] | None = None,
    status_message: str | None = None,
) -> None:
    clear_screen()
    _print_header("번역")
    print("/b 뒤로가기")
    print("/m 메인메뉴")
    print("/e 종료")
    print("-" * 60)
    print(f"소스 폴더: {source_root}")

    if step == "novel":
        print("번역할 소설 번호를 입력해 주세요.")
        for index, novel_dir in enumerate(novel_dirs, start=1):
            print(f"[{index}] {novel_dir.name}")
    elif step == "chapter" and selected_novel is not None and chapter_files is not None:
        first_num = int(chapter_files[0].stem)
        last_num = int(chapter_files[-1].stem)
        print(f"선택한 소설: {selected_novel.name}")
        print(f"발견 챕터: {len(chapter_files)}개 ({first_num:04d}~{last_num:04d})")
        print("번역할 챕터 번호 또는 범위를 입력해 주세요. (예: 3 또는 1~5)")

    print(status_message or "")
    print("-" * 60)


def render_translation_progress_screen(
    *,
    file_index: int,
    total_files: int,
    stage: str,
    current: int,
    total: int,
    source_file: Path,
    title: str,
    output_path: Path,
    status_message: str | None = None,
) -> None:
    clear_screen()
    _print_header("번역")
    print(f"파일 진행: {_build_progress_bar(file_index - 1, total_files)} {file_index}/{total_files}")
    print(f"단계: {stage}")
    print(f"청크 진행: {_build_progress_bar(current, total)} {current}/{total}")
    print("-" * 60)
    print(f"파일: {source_file.name}")
    print(f"제목: {title}")
    print(f"출력 경로: {output_path}")
    print(status_message or "")
    print("-" * 60)


def render_translation_complete_screen(
    *,
    total_files: int,
    completed_files: int,
    output_root: Path,
    last_output_path: Path | None = None,
    status_message: str | None = None,
) -> None:
    clear_screen()
    _print_header("번역")
    print("[INFO] 번역이 완료되었습니다.")
    print("-" * 60)
    print(f"완료 파일: {completed_files}/{total_files}")
    print(f"출력 폴더: {output_root}")
    if last_output_path is not None:
        print(f"마지막 결과 파일: {last_output_path}")
    if status_message:
        print("-" * 60)
        print(status_message)
    print("-" * 60)
    print("엔터를 누르면 메인 메뉴로 돌아갑니다.")
