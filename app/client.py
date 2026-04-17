from __future__ import annotations

import argparse
import json
import re
import socket
import subprocess
import time
from pathlib import Path
from urllib import error, request

from app.refiner import refine_document
from app.settings import (
    DEFAULT_CTX_SIZE,
    DEFAULT_DRAFT_TEMPERATURE,
    DEFAULT_MAX_CHARS,
    DEFAULT_N_PREDICT,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_REFINE_TEMPERATURE,
    DEFAULT_SERVER_URL,
    DEFAULT_STARTUP_TIMEOUT,
    DEFAULT_TIMEOUT,
    DEFAULT_TOP_P,
    GLOSSARY_PATH,
    LLAMA_MODEL_PATH,
    LLAMA_SERVER_PATH,
    SOURCE_PATH,
)
from app.translation import (
    TranslationConfig,
    build_draft_output_path,
    build_output_path,
    translate_document,
    validate_paths,
)
from app.ui import (
    parse_command,
    render_translation_complete_screen,
    render_translation_progress_screen,
    render_translation_selection_screen,
)
from app.utils import (
    find_chapter_files,
    find_source_novels,
    parse_chapter_selection,
    parse_source_file,
    sanitize_model_text,
)


class LlamaCppServerClient:
    def __init__(self, base_url: str, timeout: int):
        self.base_url = base_url.rstrip("/")
        self.chat_completion_url = self.base_url + "/v1/chat/completions"
        self.health_url = self.base_url + "/health"
        self.timeout = max(1, timeout)

    def wait_until_ready(self, startup_timeout: int) -> None:
        deadline = time.time() + startup_timeout
        while time.time() < deadline:
            try:
                req = request.Request(self.health_url, method="GET")
                with request.urlopen(req, timeout=5) as response:
                    if 200 <= response.status < 500:
                        return
            except Exception:
                time.sleep(1.0)
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

    def translate(self, prompt: str, *, temperature: float, top_p: float, n_predict: int) -> str:
        payload = self._build_payload(prompt, temperature=temperature, top_p=top_p, n_predict=n_predict)

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

        try:
            response_body = send(payload)
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code == 500 and ("<|channel>" in detail or "<channel|>" in detail or "Failed to parse input" in detail):
                retry_payload = self._build_payload(
                    self._sanitize_prompt(prompt).replace("<|channel>", "").replace("<channel|>", ""),
                    temperature=temperature,
                    top_p=top_p,
                    n_predict=n_predict,
                )
                try:
                    response_body = send(retry_payload)
                except error.HTTPError as retry_exc:
                    retry_detail = retry_exc.read().decode("utf-8", errors="replace")
                    raise RuntimeError(f"llama-server returned HTTP {retry_exc.code}: {retry_detail}") from retry_exc
                except (TimeoutError, socket.timeout) as retry_exc:
                    raise RuntimeError(
                        f"llama-server request timed out after {self.timeout} seconds during retry"
                    ) from retry_exc
            else:
                raise RuntimeError(f"llama-server returned HTTP {exc.code}: {detail}") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise RuntimeError(f"llama-server request timed out after {self.timeout} seconds") from exc
        except error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, TimeoutError | socket.timeout):
                raise RuntimeError(f"Failed to connect to llama-server within {self.timeout} seconds") from exc
            raise RuntimeError(f"Failed to connect to llama-server: {exc.reason}") from exc

        data = json.loads(response_body)
        choices = data.get("choices", [])
        message = choices[0].get("message", {}) if choices else {}
        content = message.get("content", "")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError(f"Empty translation response: {data}")
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
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def extract_port(server_url: str) -> int:
    match = re.search(r":(\d+)$", server_url.rstrip("/"))
    if not match:
        raise ValueError(f"Could not parse port from server URL: {server_url}")
    return int(match.group(1))


def parse_args() -> TranslationConfig:
    parser = argparse.ArgumentParser(description="Translate one crawler chapter file with llama.cpp.")
    parser.add_argument("--source", help="Source txt file to translate")
    parser.add_argument("--server-exe", help="Path to llama-server executable")
    parser.add_argument("--model", help="Path to GGUF model file")
    parser.add_argument("--server-url", default=DEFAULT_SERVER_URL, help="llama-server base URL")
    parser.add_argument("--glossary", help="Optional glossary JSON path")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="Root directory for translated output")
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help="Maximum characters per chunk")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="Request timeout in seconds")
    parser.add_argument("--draft-temperature", type=float, default=DEFAULT_DRAFT_TEMPERATURE)
    parser.add_argument("--refine-temperature", type=float, default=DEFAULT_REFINE_TEMPERATURE)
    parser.add_argument("--top-p", type=float, default=DEFAULT_TOP_P)
    parser.add_argument("--n-predict", type=int, default=DEFAULT_N_PREDICT)
    parser.add_argument("--ctx-size", type=int, default=DEFAULT_CTX_SIZE)
    parser.add_argument("--gpu-layers", type=int)
    parser.add_argument("--threads", type=int)
    parser.add_argument("--sleep", type=float, default=0.0)
    parser.add_argument("--startup-timeout", type=int, default=DEFAULT_STARTUP_TIMEOUT)
    args = parser.parse_args()

    return TranslationConfig(
        source_file=Path(args.source) if args.source else None,
        server_executable=Path(args.server_exe) if args.server_exe else LLAMA_SERVER_PATH,
        model_path=Path(args.model) if args.model else LLAMA_MODEL_PATH,
        server_url=args.server_url,
        glossary_path=Path(args.glossary) if args.glossary else GLOSSARY_PATH,
        output_root=Path(args.output_root),
        max_chunk_chars=max(300, args.max_chars),
        timeout=max(1, args.timeout),
        draft_temperature=args.draft_temperature,
        refine_temperature=args.refine_temperature,
        top_p=args.top_p,
        n_predict=max(128, args.n_predict),
        context_size=max(1024, args.ctx_size),
        gpu_layers=args.gpu_layers,
        threads=args.threads,
        sleep_seconds=max(0.0, args.sleep),
        startup_timeout=max(5, args.startup_timeout),
    )


def prompt_for_missing_paths(config: TranslationConfig) -> TranslationConfig:
    if config.server_executable is None:
        raw = input("llama-server 실행 파일 경로: ").strip().strip('"')
        config.server_executable = Path(raw) if raw else None

    if config.model_path is None:
        raw = input("GGUF 모델 파일 경로: ").strip().strip('"')
        config.model_path = Path(raw) if raw else None

    if config.glossary_path is None:
        raw = input(f"glossary.json 경로 (기본: {GLOSSARY_PATH}): ").strip().strip('"')
        config.glossary_path = Path(raw) if raw else GLOSSARY_PATH

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

            if command in {"main", "back", "exit"}:
                return []

            try:
                selected_index = int(raw)
            except ValueError:
                status_message = "[ERROR] 소설 번호를 숫자로 입력해 주세요."
                continue

            if not 1 <= selected_index <= len(novel_dirs):
                status_message = "[ERROR] 목록에 있는 소설 번호를 입력해 주세요."
                continue

            selected_novel = novel_dirs[selected_index - 1]
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
        if command == "exit":
            return []

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


def main() -> int:
    server_process = None

    try:
        config = parse_args()
        selected_source_files = (
            [config.source_file] if config.source_file is not None else prompt_for_source_files_with_ui(SOURCE_PATH)
        )
        config = prompt_for_missing_paths(config)
        if not selected_source_files:
            return 0

        render_translation_progress_screen(
            file_index=1,
            total_files=len(selected_source_files),
            stage="서버 시작",
            current=0,
            total=1,
            source_file=selected_source_files[0],
            title="llama-server 준비",
            output_path=config.output_root,
            status_message="[INFO] llama-server 시작 중...",
        )

        server_process = start_llama_server(config)
        client = LlamaCppServerClient(config.server_url, config.timeout)
        client.wait_until_ready(config.startup_timeout)

        last_output_path: Path | None = None
        for index, source_file in enumerate(selected_source_files, start=1):
            config.source_file = source_file
            validate_paths(config)

            document = parse_source_file(source_file)
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

            refine_document(
                translated_title,
                translated_chunks,
                client,
                config,
                output_path,
                progress_callback=progress_callback,
            )

        render_translation_complete_screen(
            total_files=len(selected_source_files),
            completed_files=len(selected_source_files),
            output_root=config.output_root,
            last_output_path=last_output_path,
            status_message="[INFO] 모든 번역 파일 저장이 완료되었습니다.",
        )
        input("")
        return 0
    except KeyboardInterrupt:
        print("\n[INFO] 사용자가 작업을 중단했습니다.")
        return 130
    except Exception as exc:
        print(f"[ERROR] {exc}")
        return 1
    finally:
        stop_llama_server(server_process)
