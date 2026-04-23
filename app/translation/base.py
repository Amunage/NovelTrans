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
    build_translated_document,
    translate_document,
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
    parser.add_argument("--timeout", type=int, default=runtime_settings.timeout, help="Request timeout in seconds")
    parser.add_argument("--draft-temperature", type=float, default=runtime_settings.draft_temperature)
    parser.add_argument("--refine-temperature", type=float, default=runtime_settings.refine_temperature)
    parser.add_argument(
        "--refine-enabled",
        default="on" if runtime_settings.refine_enabled else "off",
        choices=("on", "off"),
        help="Enable or disable the refinement pass",
    )
    parser.add_argument("--top-p", type=float, default=runtime_settings.top_p)
    parser.add_argument("--n-predict", type=int, default=runtime_settings.n_predict)
    parser.add_argument("--ctx-size", type=int, default=runtime_settings.ctx_size)
    parser.add_argument("--gpu-layers", type=int, default=runtime_settings.gpu_layers)
    parser.add_argument("--threads", type=int, default=runtime_settings.threads)
    parser.add_argument("--sleep", type=float, default=0.0)
    parser.add_argument("--startup-timeout", type=int, default=runtime_settings.startup_timeout)
    args = parser.parse_args()

    return TranslationConfig(
        source_file=Path(args.source) if args.source else None,
        server_executable=Path(args.server_exe) if args.server_exe else runtime_settings.llama_server_path,
        model_path=Path(args.model) if args.model else runtime_settings.llama_model_path,
        server_url=args.server_url,
        glossary_path=Path(args.glossary) if args.glossary else runtime_settings.glossary_path,
        output_root=Path(args.output_root),
        max_chunk_chars=max(300, args.max_chars),
        timeout=max(1, args.timeout),
        draft_temperature=args.draft_temperature,
        refine_temperature=args.refine_temperature,
        refine_enabled=args.refine_enabled == "on",
        top_p=args.top_p,
        n_predict=max(128, args.n_predict),
        context_size=max(1024, args.ctx_size),
        gpu_layers=args.gpu_layers,
        threads=args.threads,
        sleep_seconds=max(0.0, args.sleep),
        startup_timeout=max(5, args.startup_timeout),
    )


def main() -> int:
    server_process = None
    translation_started_at = 0.0

    try:
        config = parse_args()
        log_runtime_event("translation main start")
        runtime_settings = get_runtime_settings()
        selected_source_files = (
            [config.source_file]
            if config.source_file is not None
            else prompt_for_source_files_with_ui(runtime_settings.source_path)
        )
        config = prompt_for_missing_paths(config)
        if not selected_source_files:
            log_runtime_event("translation main cancelled | reason=no_source_files")
            return 0

        translation_started_at = time.monotonic()
        render_translation_progress_screen(
            file_index=1,
            total_files=len(selected_source_files),
            stage="모델 로드",
            current=0,
            total=1,
            elapsed_seconds=0,
            source_file=selected_source_files[0],
            title="",
            output_path=config.output_root,
            status_message=None,
        )

        server_process = start_llama_server(config)
        client = LlamaCppServerClient(config.server_url, config.timeout)
        client.wait_until_ready(
            config.startup_timeout,
            progress_callback=lambda elapsed, timeout: render_translation_progress_screen(
                file_index=1,
                total_files=len(selected_source_files),
                stage="모델 로드",
                current=min(elapsed, timeout),
                total=max(timeout, 1),
                elapsed_seconds=int(time.monotonic() - translation_started_at),
                source_file=selected_source_files[0],
                title="",
                output_path=config.output_root,
                status_message=None,
            ),
        )

        last_output_path: Path | None = None
        for index, source_file in enumerate(selected_source_files, start=1):
            log_runtime_event(f"translation file start | index={index}/{len(selected_source_files)} | source={source_file}")
            config.source_file = source_file
            validate_paths(config)

            document = parse_source_file(source_file)
            source_chunks = split_into_chunks(document.body, config.max_chunk_chars)
            draft_output_path = build_draft_output_path(source_file, config.output_root)
            output_path = build_output_path(source_file, config.output_root)
            last_output_path = output_path

            def progress_callback(stage: str, current: int, total: int, status: str | None) -> None:
                render_translation_progress_screen(
                    file_index=index,
                    total_files=len(selected_source_files),
                    stage=stage,
                    current=current,
                    total=total,
                    elapsed_seconds=int(time.monotonic() - translation_started_at),
                    source_file=source_file,
                    title=document.title,
                    output_path=output_path,
                    status_message=status,
                )

            translated_title, translated_chunks = translate_document(
                document,
                client,
                config,
                draft_output_path,
                progress_callback=progress_callback,
            )

            if config.refine_enabled:
                refine_document(
                    translated_title,
                    translated_chunks,
                    source_chunks,
                    client,
                    config,
                    output_path,
                    progress_callback=progress_callback,
                )
            else:
                progress_callback("다듬기 생략", 1, 1, "[INFO] 다듬기가 꺼져 있어 초벌 번역을 최종 결과로 저장합니다.")
                atomic_write_text(output_path, build_translated_document(translated_title, translated_chunks))
            log_runtime_event(f"translation file complete | source={source_file} | output={output_path}")

        stop_llama_server(server_process)
        server_process = None
        render_translation_complete_screen(
            total_files=len(selected_source_files),
            completed_files=len(selected_source_files),
            output_root=config.output_root,
            last_output_path=last_output_path,
            elapsed_seconds=int(time.monotonic() - translation_started_at),
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