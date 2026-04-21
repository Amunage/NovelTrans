from __future__ import annotations

import argparse
import json
import re
import socket
import subprocess
import threading
import time
import traceback
from pathlib import Path
from urllib import error, request

from app.config import get_runtime_settings, log_runtime_event
from app.controller import prompt_for_missing_paths, prompt_for_source_files_with_ui
from app.refiner import refine_document
from app.translation import (
    TranslationConfig,
    atomic_write_text,
    build_draft_output_path,
    build_output_path,
    build_translated_document,
    translate_document,
    validate_paths,
)
from app.ui import render_translation_complete_screen, render_translation_progress_screen
from app.utils import parse_source_file, sanitize_model_text, split_into_chunks


class LlamaCppServerClient:
    def __init__(self, base_url: str, timeout: int):
        self.base_url = base_url.rstrip("/")
        self.chat_completion_url = self.base_url + "/v1/chat/completions"
        self.health_url = self.base_url + "/health"
        self.timeout = max(1, timeout)

    def wait_until_ready(self, startup_timeout: int, progress_callback=None) -> None:
        log_runtime_event(f"llama health wait start | url={self.health_url} | timeout={startup_timeout}")
        start_time = time.time()
        deadline = start_time + startup_timeout
        while time.time() < deadline:
            elapsed_seconds = int(time.time() - start_time)
            if progress_callback is not None:
                progress_callback(elapsed_seconds, startup_timeout)
            try:
                req = request.Request(self.health_url, method="GET")
                with request.urlopen(req, timeout=5) as response:
                    if 200 <= response.status < 500:
                        log_runtime_event(
                            f"llama health ready | url={self.health_url} | status={response.status} | "
                            f"elapsed={int(time.time() - start_time)}"
                        )
                        return
            except Exception as exc:
                log_runtime_event(f"llama health check failed | url={self.health_url} | error={exc!r}")
                time.sleep(1.0)
        log_runtime_event(f"llama health wait timeout | url={self.health_url} | timeout={startup_timeout}")
        raise RuntimeError("llama-server did not become ready within the startup timeout")

    def _sanitize_prompt(self, text: str) -> str:
        sanitized = sanitize_model_text(text) or ""
        sanitized = re.sub(r"\u0000", "", sanitized)
        sanitized = re.sub(r"\n{3,}", "\n\n", sanitized)
        return sanitized.strip()

    def _build_payload(self, prompt: str, *, temperature: float, top_p: float, n_predict: int) -> dict:
        return {
            "messages": [{"role": "user", "content": self._sanitize_prompt(prompt)}],
            "chat_template_kwargs": {"enable_thinking": False},
            "reasoning_format": "none",
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": n_predict,
            "stop": [
                "<chapter_title>",
                "<previous_source>",
                "<previous_translation>",
                "<current_source>",
                "<previous_refined>",
                "<current_text>",
                "<title>",
                "<|channel>",
                "<channel|>",
            ],
            "stream": False,
        }

    def translate(
        self,
        prompt: str,
        *,
        temperature: float,
        top_p: float,
        n_predict: int,
        wait_callback=None,
    ) -> str:
        payload = self._build_payload(prompt, temperature=temperature, top_p=top_p, n_predict=n_predict)
        log_runtime_event(
            f"llama translate request start | url={self.chat_completion_url} | "
            f"prompt_chars={len(prompt)} | n_predict={n_predict} | temperature={temperature}"
        )

        def send(payload_to_send: dict) -> str:
            body = json.dumps(payload_to_send, ensure_ascii=False).encode("utf-8")
            req = request.Request(
                self.chat_completion_url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(req, timeout=self.timeout) as response:
                return response.read().decode("utf-8")

        def send_with_wait(payload_to_send: dict) -> str:
            result: dict[str, str] = {}
            failure: dict[str, BaseException] = {}

            def runner() -> None:
                try:
                    result["response_body"] = send(payload_to_send)
                except BaseException as exc:  # noqa: BLE001
                    failure["error"] = exc

            worker = threading.Thread(target=runner, daemon=True)
            worker.start()

            while worker.is_alive():
                worker.join(timeout=1.0)
                if worker.is_alive() and wait_callback is not None:
                    wait_callback()

            if "error" in failure:
                raise failure["error"]

            return result["response_body"]

        try:
            response_body = send_with_wait(payload)
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            log_runtime_event(
                f"llama translate http error | url={self.chat_completion_url} | code={exc.code} | detail={detail[:500]!r}"
            )
            if exc.code == 500 and ("<|channel>" in detail or "<channel|>" in detail or "Failed to parse input" in detail):
                retry_payload = self._build_payload(
                    self._sanitize_prompt(prompt).replace("<|channel>", "").replace("<channel|>", ""),
                    temperature=temperature,
                    top_p=top_p,
                    n_predict=n_predict,
                )
                try:
                    response_body = send_with_wait(retry_payload)
                except error.HTTPError as retry_exc:
                    retry_detail = retry_exc.read().decode("utf-8", errors="replace")
                    log_runtime_event(
                        f"llama translate retry http error | url={self.chat_completion_url} | "
                        f"code={retry_exc.code} | detail={retry_detail[:500]!r}"
                    )
                    raise RuntimeError(f"llama-server returned HTTP {retry_exc.code}: {retry_detail}") from retry_exc
                except (TimeoutError, socket.timeout) as retry_exc:
                    log_runtime_event(f"llama translate retry timeout | timeout={self.timeout} | error={retry_exc!r}")
                    raise RuntimeError(
                        f"llama-server request timed out after {self.timeout} seconds during retry"
                    ) from retry_exc
            else:
                raise RuntimeError(f"llama-server returned HTTP {exc.code}: {detail}") from exc
        except (TimeoutError, socket.timeout) as exc:
            log_runtime_event(f"llama translate timeout | timeout={self.timeout} | error={exc!r}")
            raise RuntimeError(f"llama-server request timed out after {self.timeout} seconds") from exc
        except error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, TimeoutError | socket.timeout):
                log_runtime_event(f"llama translate connect timeout | timeout={self.timeout} | error={exc!r}")
                raise RuntimeError(f"Failed to connect to llama-server within {self.timeout} seconds") from exc
            log_runtime_event(f"llama translate connect failed | error={exc!r}")
            raise RuntimeError(f"Failed to connect to llama-server: {exc.reason}") from exc

        data = json.loads(response_body)
        choices = data.get("choices", [])
        message = choices[0].get("message", {}) if choices else {}
        content = message.get("content", "")
        if not isinstance(content, str) or not content.strip():
            log_runtime_event(f"llama translate empty response | response={data!r}")
            raise RuntimeError(f"Empty translation response: {data}")
        log_runtime_event(
            f"llama translate request complete | url={self.chat_completion_url} | response_chars={len(content)}"
        )
        return content.strip()


def start_llama_server(config: TranslationConfig) -> subprocess.Popen:
    command = [
        str(config.server_executable),
        "-m",
        str(config.model_path),
        "-c",
        str(config.context_size),
        "--reasoning",
        "off",
        "--reasoning-format",
        "none",
        "--host",
        "127.0.0.1",
        "--port",
        str(extract_port(config.server_url)),
    ]

    if config.gpu_layers is not None:
        command.extend(["-ngl", str(config.gpu_layers)])
    if config.threads is not None:
        command.extend(["-t", str(config.threads)])

    log_runtime_event(
        f"llama server start | executable={config.server_executable} | model={config.model_path} | "
        f"url={config.server_url} | ctx={config.context_size} | gpu_layers={config.gpu_layers} | threads={config.threads}"
    )
    return subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        text=True,
    )


def stop_llama_server(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return

    process.terminate()
    try:
        process.wait(timeout=10)
        log_runtime_event("llama server stopped")
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
        log_runtime_event("llama server killed after stop timeout")


def extract_port(server_url: str) -> int:
    match = re.search(r":(\d+)$", server_url.rstrip("/"))
    if not match:
        raise ValueError(f"Could not parse port from server URL: {server_url}")
    return int(match.group(1))


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
                status_message=None
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
        input("")
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
