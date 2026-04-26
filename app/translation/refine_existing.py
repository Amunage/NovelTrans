from __future__ import annotations

import re
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

from app.server.llama import LlamaCppServerClient, start_llama_server, stop_llama_server
from app.settings.config import get_runtime_settings
from app.settings.logging import log_runtime_event
from app.translation.engine import (
    TranslationConfig,
    atomic_write_text,
    build_review_document,
    build_review_output_path,
    build_translated_document,
    validate_glossary_file,
    validate_paths,
)
from app.translation.refine import refine_document
from app.ui.control import parse_command, prompt_for_missing_paths, wait_for_enter
from app.ui.render import (
    render_refine_chapter_selection_screen,
    render_refine_complete_screen,
    render_refine_selection_screen,
    render_translation_progress_screen,
)
from app.ui.validators import validate_menu_number
from app.utils import (
    find_translated_chapters,
    find_translated_novels,
    parse_chapter_selection,
    parse_source_file,
    split_into_chunks,
)


@dataclass(frozen=True)
class RefineChunkPlan:
    translated_title: str
    translated_chunks: list[str]
    source_chunks: list[str | None]
    source_file: Path | None


def _split_paragraphs(text: str) -> list[str]:
    return [paragraph.strip() for paragraph in re.split(r"\n{2,}", text) if paragraph.strip()]


def _paragraph_line_counts(paragraphs: list[str]) -> list[int]:
    return [len(paragraph.splitlines()) for paragraph in paragraphs]


def _is_matching_source(source_body: str, translated_body: str) -> bool:
    source_paragraphs = _split_paragraphs(source_body)
    translated_paragraphs = _split_paragraphs(translated_body)
    return (
        bool(source_paragraphs)
        and len(source_paragraphs) == len(translated_paragraphs)
        and _paragraph_line_counts(source_paragraphs) == _paragraph_line_counts(translated_paragraphs)
    )


def _build_source_path(translated_file: Path, source_root: Path) -> Path:
    source_stem = translated_file.stem[:-3] if translated_file.stem.endswith("_ko") else translated_file.stem
    return source_root / translated_file.parent.name / f"{source_stem}.txt"


def _build_aligned_chunks(source_body: str, translated_body: str, max_chunk_chars: int) -> tuple[list[str], list[str]]:
    source_paragraphs = _split_paragraphs(source_body)
    translated_paragraphs = _split_paragraphs(translated_body)
    source_chunks: list[str] = []
    translated_chunks: list[str] = []
    current_source: list[str] = []
    current_translated: list[str] = []
    current_length = 0

    for source_paragraph, translated_paragraph in zip(source_paragraphs, translated_paragraphs):
        addition = len(translated_paragraph) if not current_translated else len(translated_paragraph) + 2
        if current_translated and current_length + addition > max_chunk_chars:
            source_chunks.append("\n\n".join(current_source))
            translated_chunks.append("\n\n".join(current_translated))
            current_source = [source_paragraph]
            current_translated = [translated_paragraph]
            current_length = len(translated_paragraph)
        else:
            current_source.append(source_paragraph)
            current_translated.append(translated_paragraph)
            current_length += addition

    if current_translated:
        source_chunks.append("\n\n".join(current_source))
        translated_chunks.append("\n\n".join(current_translated))

    return translated_chunks, source_chunks


def _build_refine_plan(translated_file: Path, config: TranslationConfig, source_root: Path) -> RefineChunkPlan:
    translated_document = parse_source_file(translated_file)
    source_file = _build_source_path(translated_file, source_root)

    if source_file.is_file():
        source_document = parse_source_file(source_file)
        if _is_matching_source(source_document.body, translated_document.body):
            translated_chunks, source_chunks = _build_aligned_chunks(
                source_document.body,
                translated_document.body,
                config.max_chunk_chars,
            )
            return RefineChunkPlan(
                translated_title=translated_document.title,
                translated_chunks=translated_chunks,
                source_chunks=source_chunks,
                source_file=source_file,
            )

    translated_chunks = split_into_chunks(translated_document.body, config.max_chunk_chars)
    return RefineChunkPlan(
        translated_title=translated_document.title,
        translated_chunks=translated_chunks,
        source_chunks=[None] * len(translated_chunks),
        source_file=None,
    )


def _count_matching_sources(chapter_files: list[Path], source_root: Path) -> int:
    count = 0
    for translated_file in chapter_files:
        source_file = _build_source_path(translated_file, source_root)
        if not source_file.is_file():
            continue
        try:
            source_document = parse_source_file(source_file)
            translated_document = parse_source_file(translated_file)
        except Exception:
            continue
        if _is_matching_source(source_document.body, translated_document.body):
            count += 1
    return count


def _select_refine_targets(output_root: Path, source_root: Path) -> list[Path]:
    status_message: str | None = None

    while True:
        novel_dirs = find_translated_novels(output_root)
        if not novel_dirs:
            render_refine_selection_screen(
                output_root=output_root,
                novel_dirs=[],
                status_message=f"[WARN] 다듬을 번역 폴더가 없습니다: {output_root}",
            )
            wait_for_enter()
            return []

        render_refine_selection_screen(output_root=output_root, novel_dirs=novel_dirs, status_message=status_message)
        raw = input("").strip()
        command = parse_command(raw)
        if command == "back":
            return []

        status_message = validate_menu_number(raw, len(novel_dirs))
        if status_message is not None:
            continue

        selected_novel = novel_dirs[int(raw) - 1]
        chapter_files = find_translated_chapters(selected_novel)
        if not chapter_files:
            status_message = f"[WARN] 다듬을 번역 파일이 없습니다: {selected_novel}"
            continue

        chapter_status: str | None = None
        while True:
            render_refine_chapter_selection_screen(
                novel_dir=selected_novel,
                chapter_files=chapter_files,
                source_match_count=_count_matching_sources(chapter_files, source_root),
                status_message=chapter_status,
            )
            chapter_raw = input("").strip()
            chapter_command = parse_command(chapter_raw)
            if chapter_command == "back":
                status_message = None
                break

            selection = parse_chapter_selection(chapter_raw)
            if selection is None:
                chapter_status = "[ERROR] 3 또는 1~5, 1-5 형식으로 입력해 주세요."
                continue

            start_index, end_index = selection
            if start_index < 1 or end_index > len(chapter_files):
                chapter_status = f"[ERROR] 1~{len(chapter_files)} 범위 안에서 입력해 주세요."
                continue

            return chapter_files[start_index - 1 : end_index]


def _build_config() -> TranslationConfig:
    settings = get_runtime_settings()
    return TranslationConfig(
        source_file=None,
        server_executable=settings.llama_server_path,
        model_path=settings.llama_model_path,
        server_url=settings.server_url,
        glossary_path=settings.glossary_path,
        output_root=settings.output_root,
        max_chunk_chars=settings.max_chars,
        request_timeout=settings.request_timeout,
        draft_temperature=settings.draft_temperature,
        refine_temperature=settings.refine_temperature,
        auto_refine=settings.auto_refine,
        top_p=settings.top_p,
        max_tokens=settings.max_tokens,
        context_size=settings.ctx_size,
        gpu_layers=settings.gpu_layers,
        threads=settings.threads,
        sleep_seconds=0.0,
        startup_timeout=settings.startup_timeout,
        debug_mode=settings.debug_mode,
    )


def main() -> int:
    server_process = None
    started_at = 0.0

    try:
        settings = get_runtime_settings()
        config = prompt_for_missing_paths(_build_config())
        selected_files = _select_refine_targets(settings.output_root, settings.source_path)
        if not selected_files:
            log_runtime_event("refine existing cancelled | reason=no_selected_files")
            return 0

        glossary_warning = validate_glossary_file(config.glossary_path)
        if glossary_warning is not None:
            print(glossary_warning)
            wait_for_enter()
            return 0

        config.source_file = selected_files[0]
        validate_paths(config)

        render_translation_progress_screen(
            file_index=1,
            total_files=len(selected_files),
            stage="모델 로드",
            current=0,
            total=1,
            elapsed_seconds=0,
            source_tokens_per_second=None,
            source_file=selected_files[0],
            title="",
            output_path=settings.output_root,
            screen_title="번역 다듬기",
            status_message=None,
        )
        server_process = start_llama_server(config)
        client = LlamaCppServerClient(config.server_url, config.request_timeout)
        client.wait_until_ready(
            config.startup_timeout,
            progress_callback=lambda elapsed, timeout: render_translation_progress_screen(
                file_index=1,
                total_files=len(selected_files),
                stage="모델 로드",
                current=min(elapsed, timeout),
                total=max(timeout, 1),
                elapsed_seconds=int(elapsed),
                source_tokens_per_second=None,
                source_file=selected_files[0],
                title="",
                output_path=settings.output_root,
                screen_title="번역 다듬기",
                status_message=None,
            ),
        )

        started_at = time.monotonic()
        total_generated_output_tokens = 0
        displayed_tokens_per_second: float | None = None
        last_output_path: Path | None = None

        for file_index, translated_file in enumerate(selected_files, start=1):
            log_runtime_event(f"refine existing file start | index={file_index}/{len(selected_files)} | file={translated_file}")
            config.source_file = translated_file
            plan = _build_refine_plan(translated_file, config, settings.source_path)
            last_output_path = translated_file

            def progress_callback(stage: str, current: int, total: int, status: str | None) -> None:
                elapsed = time.monotonic() - started_at
                render_translation_progress_screen(
                    file_index=file_index,
                    total_files=len(selected_files),
                    stage=stage,
                    current=current,
                    total=total,
                    elapsed_seconds=int(elapsed),
                    source_tokens_per_second=displayed_tokens_per_second,
                    source_file=translated_file,
                    title=plan.translated_title,
                    output_path=translated_file,
                    screen_title="번역 다듬기",
                    status_message=status,
                )

            def output_callback(
                stage: str,
                current: int,
                total: int,
                output_tokens: int,
                chunk_elapsed_seconds: float,
                status: str | None,
            ) -> None:
                nonlocal displayed_tokens_per_second, total_generated_output_tokens
                total_generated_output_tokens += output_tokens
                displayed_tokens_per_second = (
                    output_tokens / chunk_elapsed_seconds if chunk_elapsed_seconds > 0 and output_tokens > 0 else None
                )
                progress_callback(stage, current, total, status)

            refined_title, refined_chunks = refine_document(
                plan.translated_title,
                plan.translated_chunks,
                plan.source_chunks,
                client,
                config,
                progress_callback=progress_callback,
                output_callback=output_callback,
            )
            atomic_write_text(translated_file, build_translated_document(refined_title, refined_chunks))
            if plan.source_file is not None and all(chunk is not None for chunk in plan.source_chunks):
                review_path = build_review_output_path(plan.source_file, settings.output_root)
                atomic_write_text(review_path, build_review_document([chunk for chunk in plan.source_chunks if chunk], refined_chunks))
            log_runtime_event(f"refine existing file complete | file={translated_file}")

        stop_llama_server(server_process)
        server_process = None
        render_refine_complete_screen(
            total_files=len(selected_files),
            completed_files=len(selected_files),
            output_root=settings.output_root,
            last_output_path=last_output_path,
            status_message="[INFO] 번역 다듬기가 완료되었습니다.",
        )
        wait_for_enter()
        return 0
    except KeyboardInterrupt:
        log_runtime_event("refine existing cancelled by user")
        print("\n[INFO] 사용자가 작업을 중단했습니다.")
        return 130
    except Exception as exc:
        log_runtime_event(f"refine existing failed | error={exc!r}\n{traceback.format_exc()}")
        print(f"[ERROR] {exc}")
        return 1
    finally:
        stop_llama_server(server_process)
