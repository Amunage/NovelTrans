from __future__ import annotations

import json
import re
import socket
import subprocess
import threading
import time
from typing import Protocol
from urllib import error, request

from app.settings.logging import log_runtime_event
from app.utils import sanitize_model_text


class ServerRuntimeConfig(Protocol):
    server_executable: object
    model_path: object
    server_url: str
    context_size: int
    gpu_layers: int | None
    threads: int | None


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

    def _build_payload(self, prompt: str, *, temperature: float, top_p: float, max_tokens: int) -> dict:
        return {
            "messages": [{"role": "user", "content": self._sanitize_prompt(prompt)}],
            "chat_template_kwargs": {"enable_thinking": False},
            "reasoning_format": "none",
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
            "stop": [
                "<chapter_title>",
                "<previous_source>",
                "<next_source>",
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
        max_tokens: int,
        wait_callback=None,
    ) -> tuple[str, int]:
        payload = self._build_payload(prompt, temperature=temperature, top_p=top_p, max_tokens=max_tokens)
        log_runtime_event(
            f"llama translate request start | url={self.chat_completion_url} | "
            f"prompt_chars={len(prompt)} | max_tokens={max_tokens} | temperature={temperature}"
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
                    max_tokens=max_tokens,
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
        usage = data.get("usage", {})
        completion_tokens = usage.get("completion_tokens")
        if not isinstance(content, str):
            log_runtime_event(f"llama translate empty response | response={data!r}")
            raise RuntimeError(f"Empty translation response: {data}")
        if not isinstance(completion_tokens, int) or completion_tokens <= 0:
            log_runtime_event(f"llama translate missing completion tokens | response={data!r}")
            raise RuntimeError("llama-server response is missing usage.completion_tokens")
        if not content.strip():
            log_runtime_event(
                f"llama translate blank content | url={self.chat_completion_url} | completion_tokens={completion_tokens}"
            )
        log_runtime_event(
            f"llama translate request complete | url={self.chat_completion_url} | response_chars={len(content)} | completion_tokens={completion_tokens}"
        )
        return content.strip(), completion_tokens


def start_llama_server(config: ServerRuntimeConfig) -> subprocess.Popen:
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
