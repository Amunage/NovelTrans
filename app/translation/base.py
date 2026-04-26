from __future__ import annotations

import argparse
import time
import traceback
from pathlib import Path

from app.server.llama import LlamaCppServerClient, start_llama_server, stop_llama_server
from app.settings.config import get_runtime_settings
from app.settings.logging import log_runtime_event
from app.ui.control import prompt_for_missing_paths, prompt_for_source_files_with_ui, wait_for_enter
from app.translation.engine import (
    TranslationConfig,
    atomic_write_text,
    build_draft_output_path,
    build_output_path,
    build_review_document,
    build_review_output_path,
    build_translated_document,
    translate_document,
    validate_glossary_file,
    validate_paths,
)
from app.translation.refine import refine_document
from app.ui import render_translation_complete_screen, render_translation_progress_screen
from app.utils import parse_source_file, split_into_chunks


def parse_args() -> TranslationConfig:
    runtime_settings = get_runtime_settings()
    parser = argparse.ArgumentParser(description="Translate one crawler chapter file with llama.cpp.")
    parser.add_argument("--source", help="Source txt file to translate")
    parser.add_argument("--server-exe", help="Path to llama-server executable")
    parser.add_argument("--model", help="Path to GGUF model file")
    parser.add_argument("--server-url", default=runtime_settings.server_url, help="llama-server base URL")
    parser.add_argument("--glossary", help="Optional glossary JSON path")
    parser.add_argument("--output-root", default=str(runtime_settings.output_root), help="Root directory for translated output")
    parser.add_argument("--max-chars", type=int, default=runtime_settings.max_chars, help="Maximum characters per chunk")
    parser.add_argument(
        "--request-timeout",
        type=int,
        default=runtime_settings.request_timeout,
        help="Model request timeout in seconds",
    )
    parser.add_argument("--draft-temperature", type=float, default=runtime_settings.draft_temperature)
    parser.add_argument("--refine-temperature", type=float, default=runtime_settings.refine_temperature)
    parser.add_argument(
        "--auto-refine",
        default="true" if runtime_settings.auto_refine else "false",
        choices=("true", "false"),
        help="Enable or disable the refinement pass",
    )
    parser.add_argument("--top-p", type=float, default=runtime_settings.top_p)
    parser.add_argument("--max-tokens", type=int, default=runtime_settings.max_tokens)
    parser.add_argument("--ctx-size", type=int, default=runtime_settings.ctx_size)
    parser.add_argument("--gpu-layers", type=int, default=runtime_settings.gpu_layers)
    parser.add_argument("--threads", type=int, default=runtime_settings.threads)
    parser.add_argument("--sleep", type=float, default=0.0)
    parser.add_argument("--startup-timeout", type=int, default=runtime_settings.startup_timeout)
    parser.add_argument(
        "--debug-mode",
        default="true" if runtime_settings.debug_mode else "false",
        choices=("true", "false"),
        help="Show the final model prompt in the translation progress UI",
    )
    args = parser.parse_args()

    return TranslationConfig(
        source_file=Path(args.source) if args.source else None,
        server_executable=Path(args.server_exe) if args.server_exe else runtime_settings.llama_server_path,
        model_path=Path(args.model) if args.model else runtime_settings.llama_model_path,
        server_url=args.server_url,
        glossary_path=Path(args.glossary) if args.glossary else runtime_settings.glossary_path,
        output_root=Path(args.output_root),
        max_chunk_chars=max(300, args.max_chars),
        request_timeout=max(1, args.request_timeout),
        draft_temperature=args.draft_temperature,
        refine_temperature=args.refine_temperature,
        auto_refine=args.auto_refine == "true",
        top_p=args.top_p,
        max_tokens=max(128, args.max_tokens),
        context_size=max(1024, args.ctx_size),
        gpu_layers=args.gpu_layers,
        threads=args.threads,
        sleep_seconds=max(0.0, args.sleep),
        startup_timeout=max(5, args.startup_timeout),
        debug_mode=args.debug_mode == "true",
    )


def main() -> int:
    server_process = None
    translation_started_at = 0.0

    try:
        config = parse_args()
        log_runtime_event("translation main start")
        runtime_settings = get_runtime_settings()
        selected_glossary_path: Path | None = None
        if config.source_file is not None:
            selected_source_files = [config.source_file]
        else:
            selected_source_files, selected_glossary_path = prompt_for_source_files_with_ui(runtime_settings.source_path)
            if selected_glossary_path is not None:
                config.glossary_path = selected_glossary_path
        config = prompt_for_missing_paths(config)
        if not selected_source_files:
            log_runtime_event("translation main cancelled | reason=no_source_files")
            return 0

        glossary_warning = validate_glossary_file(config.glossary_path)
        if glossary_warning is not None:
            log_runtime_event(f"translation main blocked | reason=invalid_glossary | path={config.glossary_path}")
            print(glossary_warning)
            wait_for_enter()
            return 0

        translation_started_at = 0.0
        total_generated_output_tokens = 0
        render_translation_progress_screen(
            file_index=1,
            total_files=len(selected_source_files),
            stage="모델 로드",
            current=0,
            total=1,
            elapsed_seconds=0,
            source_tokens_per_second=None,
            source_file=selected_source_files[0],
            title="",
            output_path=config.output_root,
            status_message=None,
        )

        server_process = start_llama_server(config)
        client = LlamaCppServerClient(config.server_url, config.request_timeout)
        client.wait_until_ready(
            config.startup_timeout,
            progress_callback=lambda elapsed, timeout: render_translation_progress_screen(
                file_index=1,
                total_files=len(selected_source_files),
                stage="모델 로드",
                current=min(elapsed, timeout),
                total=max(timeout, 1),
                elapsed_seconds=int(elapsed),
                source_tokens_per_second=None,
                source_file=selected_source_files[0],
                title="",
                output_path=config.output_root,
                status_message=None,
            ),
        )
        translation_started_at = time.monotonic()

        last_output_path: Path | None = None
        for index, source_file in enumerate(selected_source_files, start=1):
            log_runtime_event(f"translation file start | index={index}/{len(selected_source_files)} | source={source_file}")
            config.source_file = source_file
            validate_paths(config)

            document = parse_source_file(source_file)
            source_chunks = split_into_chunks(document.body, config.max_chunk_chars)
            draft_output_path = build_draft_output_path(source_file, config.output_root)
            output_path = build_output_path(source_file, config.output_root)
            review_output_path = build_review_output_path(source_file, config.output_root)
            last_output_path = output_path
            displayed_tokens_per_second: float | None = None

            def progress_callback(stage: str, current: int, total: int, status: str | None) -> None:
                elapsed = time.monotonic() - translation_started_at
                render_translation_progress_screen(
                    file_index=index,
                    total_files=len(selected_source_files),
                    stage=stage,
                    current=current,
                    total=total,
                    elapsed_seconds=int(elapsed),
                    source_tokens_per_second=displayed_tokens_per_second,
                    source_file=source_file,
                    title=document.title,
                    output_path=output_path,
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

            translated_title, translated_chunks = translate_document(
                document,
                client,
                config,
                progress_callback=progress_callback,
                output_callback=output_callback,
            )
            atomic_write_text(draft_output_path, build_translated_document(translated_title, translated_chunks))

            if config.auto_refine:
                refined_title, refined_chunks = refine_document(
                    translated_title,
                    translated_chunks,
                    source_chunks,
                    client,
                    config,
                    progress_callback=progress_callback,
                    output_callback=output_callback,
                )
                atomic_write_text(output_path, build_translated_document(refined_title, refined_chunks))
                atomic_write_text(review_output_path, build_review_document(source_chunks, refined_chunks))
            else:
                progress_callback("다듬기 생략", 1, 1, "[INFO] 다듬기가 꺼져 있어 원문 번역을 최종 결과로 저장합니다.")
                atomic_write_text(output_path, build_translated_document(translated_title, translated_chunks))
                atomic_write_text(review_output_path, build_review_document(source_chunks, translated_chunks))
            log_runtime_event(f"translation file complete | source={source_file} | output={output_path}")

        stop_llama_server(server_process)
        server_process = None
        elapsed = time.monotonic() - translation_started_at
        elapsed_seconds = int(elapsed)
        average_source_tokens_per_second = (
            total_generated_output_tokens / elapsed if elapsed > 0 and total_generated_output_tokens > 0 else None
        )
        render_translation_complete_screen(
            total_files=len(selected_source_files),
            completed_files=len(selected_source_files),
            output_root=config.output_root,
            last_output_path=last_output_path,
            elapsed_seconds=elapsed_seconds,
            average_source_tokens_per_second=average_source_tokens_per_second,
            status_message="[INFO] 모든 번역 파일 처리가 완료되었습니다.",
        )
        wait_for_enter()
        log_runtime_event(f"translation main complete | files={len(selected_source_files)}")
        return 0
    except KeyboardInterrupt:
        log_runtime_event("translation cancelled by user")
        print("\n[INFO] 사용자가 작업을 중단했습니다.")
        return 130
    except Exception as exc:
        log_runtime_event(f"translation main failed | error={exc!r}\n{traceback.format_exc()}")
        print(f"[ERROR] {exc}")
        return 1
    finally:
        stop_llama_server(server_process)
